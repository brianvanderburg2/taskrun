""" Main task runner code. """

from __future__ import absolute_import

__author__ = "Brian Allen Vanderburg II"
__copyright__ = "Copyright (C) 2018 Brian Allen Vanderburg II"
__license__ = "Apache License 2.0"


# Imports
import os
import sys
import glob
import re
import types
import subprocess
import collections
import argparse

try:
    StringTypes = types.StringTypes
except AttributeError:
    StringTypes = str


# Some errors

class Error(Exception):
    pass

class ScriptError(Error):
    pass

class VariableError(ScriptError):
    pass

class CommandError(ScriptError):
    pass

class ShellError(CommandError):
    pass


# Some types used in command scripts

class Literal(object):
    """ Represent a literal value. """

    def __init__(self, value):
        self._value = value

    def __str__(self):
        return str(self._value)

class RunResult(object):

    def __init__(self, stdout, stderr, retcode, _result):
        self.stdout = stdout
        self.stderr = stderr
        self.retcode = retcode
        self._result = _result

    def __nonzero__(self):
        return self._result

    __bool__ = __nonzero__


# Script objects for the program

class Environment(object):
    """ A task environment. """

    NONE = 0
    STDOUT = 1
    STDERR = 2
    STDERROUT = 4

    def __init__(self):
        """ Initialize  the environmnet. """
        self._tasks = {}
        self._funcs = {}
        self._variables = {}
        self._variable_stack = []
        self._script_stack = []

    def _load(self, filename):
        """ Load the filename. """
        with open(filename, "rU") as handle:
            code = handle.read()

        codeobj = compile(code, filename, "exec", dont_inherit=True)


        data = dict(self._get_script_globals())
        data["__file__"] = filename

        self._script_stack.append(filename)
        exec(codeobj, data, data)
        self._script_stack.pop()

    def _get_script_globals(self):
        return {
            "env": self,
            "Error": Error,
            "Literal": Literal
        }

    def __enter__(self):
        """ Save the current variable stack. """
        self.push()

    def __exit__(self, type, value, traceback):
        """ Restore the variable stack. """
        self.pop()

    def push(self, **vars):
        """ Save the variable stack. """
        self._variable_stack.append(dict(self._variables))
        self._variables.update(vars)

    def pop(self):
        """ Restore the variable stack. """
        self._variables = self._variable_stack.pop()

    def __setitem__(self, name, value):
        """ Set a variable value. """
        self._variables[name] = value

    def __getitem__(self, name):
        """ Get a variable value. """
        if name in self._variables:
            return self._variables[name]

        raise VariableError(name)

    def __contains__(self, name):
        """ Test for a variable. """
        return name in self._variables

    def update(self, **vars):
        self._variables.update(vars)

    def evaluate(self, variable):
        """ Evaluate a variable. """
        return self.subst(self[variable])

    def subst(self, value, escape=False):
        """ Perform string substitution based on environment variables or escape values. """

        if isinstance(value, Literal):
            return value._value
        elif isinstance(value, tuple):
            return tuple(self.subst(i, escape) for i in value)
        elif isinstance(value, list):
            return list(self.subst(i, escape) for i in value)
        elif isinstance(value, dict):
            return {i: self.subst(value[i], escape) for i in value}
        elif isinstance(value, StringTypes):
            if escape:
                return re.sub(r"\$", "$$", value)
            else:
                def subfn(mo):
                    var = mo.group(0)

                    if var == "$$":
                        return "$"

                    return self.evaluate(var[2:-1])
                return re.sub(r"\$\$|\$\(\w*?\)", subfn, value)
        else:
            return value

    def escape(self, value):
        """ Return an escaped value for subst. """
        return self.subst(value, escape=True)

    def task(self, name=None, once=False, extend=False, **vars):
        """ Decorator to register a task. """
        def wrapper(fn):
            if name is not None:
                _name = name
            else:
                _name = fn.__name__

            entries = self._tasks.setdefault(_name, [])
            if len(entries) and not extend:
                raise Error("Task already defined: {0}".format(_name))

            entries.append(Task(self, fn, once, vars))

            return fn
        return wrapper

    def calltask(self, name, **vars):
        """ Call a task object. """
        if name in self._tasks:
            for entry in self._tasks[name]:
                entry.execute(vars)
        else:
            raise Error("No such task: {0}".format(name))

    def func(self, name=None):
        """ Decorator to register a function. """
        def wrapper(fn):
            if name is not None:
                _name = name
            else:
                _name = fn.__name__

            if name in self._funcs:
                raise Error("Function already defined: {0}".format(_name))

            self._funcs[_name] = fn
            return fn
        return wrapper

    def callfunc(self, name, *args, **kwargs):
        """ Call a registered function. """
        if name in self._funcs:
            return self._funcs[name](*args, **kwargs)
        else:
            raise Error("No such function: {0}".format(name))

    def include(self, *patterns):
        """ Include a file. """
        for pattern in patterns:
            fullglob = os.path.join(
                os.path.dirname(self._script_stack[-1]),
                self.subst(pattern)
            )

            for entry in sorted(glob.glob(fullglob)):
                self._load(entry)

    def capture(self, command, quite=None, abort=True, capture=STDOUT, retvals=(0,)):
        result = self.run(command, quite, abort, capture, retvals)
        return result.stdout

    def run(self, command, quite=None, abort=True, capture=NONE, retvals=(0,)):
        """ Run a command and return the results. """

        # Determine the shell to use
        shell = None
        if "TASKRUN_SHELL" in self:
            shell = self.evaluate("TASKRUN_SHELL")

        # Determine any changes to the shell environment
        shellenv = dict(os.environ)
        if "TASKRUN_SHELLENV" in self:
            env = self.evaluate("TASKRUN_SHELLENV")
            if isinstance(env, dict):
                for name in env:
                    shellenv[name] = env[name]

        # Print the command if needed
        command = self.subst(command)

        if quite is None and "TASKRUN_QUITE" in self:
            quite = bool(self.evaluate("TASKRUN_QUITE"))

        if not quite:
            self.outputln(command)

        # Run the command
        stdout = stderr = None

        if capture & self.STDOUT or capture & self.STDERROUT:
            stdout = subprocess.PIPE

        if capture & self.STDERR:
            stderr = subprocess.PIPE
        elif capture & self.STDERROUT:
            stderr = subprocess.STDOUT

        process = subprocess.Popen(
            command,
            executable=shell,
            stdout=stdout,
            stderr=stderr,
            shell=True,
            env=shellenv
        )
        (stdout, stderr) = process.communicate()
        if process.returncode not in retvals and abort:
            raise CommandError("Unexpected return value")

        return RunResult(
            stdout.decode() if stdout is not None else None,
            stderr.decode() if stderr is not None else None,
            process.returncode,
            bool(process.returncode in retvals)
        )

    def output(self, message):
        sys.stdout.write(self.subst(message))
        sys.stdout.flush()

    def outputln(self, message):
        self.output(message + "\n")

    def error(self, message):
        sys.stderr.write(self.subst(message))
        sys.stderr.flush()

    def errorln(self, message):
        self.error(message + "\n")

    def abort(self, message=None, retcode=-1):
        if message is not None:
            self.errorln(message)
        self.exit(retcode)

    def exit(self, retcode=0):
        sys.exit(retcode)


class Task(object):
    """ Represent a task to be called. """

    def __init__(self, env, fn, once, args):
        self._env = env
        self._fn = fn
        self._once = once
        self._vars = dict(args)
        self._called = False

    def execute(self, vars):
        if self._once and self._called:
            return

        with self._env:
            self._env.update(**self._vars)
            self._env.update(**vars)

            self._fn()
            self._called = True


class App(object):
    """ An object/wrapper around application-related functions. """

    def __init__(self):
        self.env = Environment()
        self.cwd = os.getcwd()
        self.cmdline = None
        self.taskfile = None

    def parse_args(self):
        """ Parse command line. """
        parser = argparse.ArgumentParser()

        parser.add_argument(
            "-f", "--file", dest="file", default="TaskFile",
            help="Specify an alternative name for TaskFile."
        )
        parser.add_argument(
            "-d", "--dir", dest="dir", default=os.getcwd(),
            help="Specify a starting directory."
        )
        parser.add_argument(
            "-l", "--list", dest="list", default=False,
            action="store_true", help="List tasks."
        )

        parser.add_argument(
            "params", nargs="*",
            help="""Parameters in the form of <taskname>, <VAR>=<VALUE>, or
                  <taskname>:<VAR>=<VALUE>[<VAR>=<VALUE>...]"""
        )

        self.cmdline = parser.parse_args()

    def find_taskfile(self):
        """ Find the task file. """
        filename = self.cmdline.file
        curdir = self.cmdline.dir

        while True:
            taskfile = os.path.join(curdir, filename)
            if os.path.isfile(taskfile):
                self.taskfile = taskfile
                return

            (head, _) = os.path.split(curdir)
            if head and head != curdir:
                curdir = head
            else:
                break

        self.taskfile = None

    def get_tasks_params(self):
        """ Return the tasks and parameters. """
        params = {}
        tasks = []

        for cmdparam in self.cmdline.params:
            if ":" in cmdparam:
                # task:NAME=VALUE:NAME=VALUE:NAME=VALUE
                parts = cmdparam.split(":")
                taskparams = {}
                for taskparam in parts[1:]:
                    if "=" in taskparam:
                        (name, value) = taskparam.split("=", 1)
                        taskparams[name] = value

                tasks.append((parts[0], taskparams))
            elif "=" in cmdparam:
                # NAME=VALUE
                (name, value) = cmdparam.split("=", 1)
                params[name] = value
            else:
                # taskname
                tasks.append((cmdparam, {}))

        return (tasks, params)

    def main(self):
        """ Run the main application. """

        env = self.env

        # Initial setup
        self.parse_args()
        self.find_taskfile()

        if self.taskfile is None:
            env.abort("Unable to find {0}".format(self.cmdline.file))

        # Set command line NAME=VALUE variables before loading the file
        (tasks, params) = self.get_tasks_params()
        env.update(**params)

        env["TOP"] = os.path.dirname(self.taskfile)
        env["ABSTOP"] = os.path.abspath(env["TOP"])
        env["CWD"] = os.path.abspath(self.cwd)
        env._load(self.taskfile)

        # Print tasks list if requested
        if self.cmdline.list:
            for name in env._tasks:
                if not name.startswith("_"):
                    env.outputln(name)
            env.exit()

        # Execute the requested tasks setting task specific variables
        for (task, params) in tasks:
            env.calltask(task, **params)

    def run(self):
        """ Run the application. """
        try:
            self.main()
        except SystemExit:
            raise
        except KeyboardInterrupt:
            pass
        except:
            (type, value, tb) = sys.exc_info()
            env = self.env

            env.errorln("{0}({1})".format(type.__name__, str(value)))
            stack = []
            while tb:
                stack.append(tb)
                tb = tb.tb_next

            for tb in reversed(stack):
                lineno = tb.tb_lineno

                fname = tb.tb_frame.f_code.co_filename
                if fname[0:1] == "<":
                    fglobals = tb.tb_frame.f_globals
                    if "__file__" in fglobals:
                        fname = fglobals["__file__"]

                env.errorln("  {0}:{1}".format(fname, lineno))
            env.abort("Aborting due to errors.")


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()

