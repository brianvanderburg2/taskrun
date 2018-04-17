""" Main task runner code. """

from __future__ import absolute_import

__author__      =   "Brian Allen Vanderburg II"
__copyright__   =   "Copyright (C) 2018 Brian Allen Vanderburg II"
__license__     =   "Apache License 2.0"


# Imports
import os
import sys
import glob
import re
import types
import subprocess
import shlex
import collections
import string
import inspect

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

RunResult = collections.namedtuple("RunResult", ["stdout", "stderr", "retcode"])

class Literal(object):
    """ Represent a literal value. """

    def __init__(self, value):
        self._value = value


# Script objects for the program

class Environment(object):
    """ A task environment. """

    def __init__(self):
        """ Initialize  the environmnet. """
        self._tasks = {}
        self._variables = {}
        self._variable_stack = []

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

    def subst(self, value):
        """ Perform string substitution based on environment variables. """

        if isinstance(value, Literal):
            return value._value
        elif isinstance(value, tuple):
            return tuple(self.subst(i) for i in value)
        elif isinstance(value, list):
            return list(self.subst(i) for i in value)
        elif isinstance(value, dict):
            return { i: self.subst(value[i]) for i in value }
        elif isinstance(value, StringTypes):
            def subfn(mo):
                var = mo.group(0)

                if var == "$$":
                    return "$"

                return self.evaluate(var[2:-1])
            return re.sub(r"\$\$|\$\(\w*?\)", subfn, value)
        else:
            return value

    def task(self, script, fn, name, **vars):
        """ Register our taskobject. """
        # TODO: warning or error if same name already exists
        self._tasks[name] = Task(script, fn, vars)

    def call(self, task, **vars):
        """ Call a task object. """
        if task in self._tasks:
            return self._tasks[task].execute(vars)
        # TODO: error if calling a task that doesn't exist

    def include(self, filename, **vars):
        """ Include a file. """
        with self:
            self.update(**vars)
            ScriptFile(self, filename)


    def output(self, message, handle=sys.stdout):
        handle.write(message)
        handle.flush()

    def outputln(self, message, handle=sys.stdout):
        handle.write(message)
        handle.write("\n")
        handle.flush()

    def abort(self, message):
        self.outputln(message, sys.stderr)
        sys.exit(-1)

    def error(self, message):
        self.outputln(message, sys.stderr)

    def info(self, message):
        self.outputln(message)



class ScriptFile(object):
    """ Represent a script file. """

    NONE = 0
    STDOUT = 1
    STDERR = 2
    STDERROUT = 4

    def __init__(self, env, filename):
        """ Initialize our script file object. """
        self._env = env
        self._filename = filename

        try:
            with open(filename, "rU") as handle:
                code = handle.read()

            codeobj = compile(code, filename, "exec", dont_inherit=True)


            data = dict(self._get_script_globals())
            data["__file__"] = filename

            exec(code, data, data)
        except Error as e:
            raise
        except Exception as e:
            raise Error("{0}:{1}\n{2}".format(type(e).__name__, filename, str(e)))

    def _get_script_globals(self):
        return {
            "env": self,
            "Error": Error,
            "Literal": Literal
        }

    # Functions to proxy to the environment.

    def __getitem__(self, name):
        return self._env[name]
    
    def __setitem__(self, name, value):
        self._env[name] = value

    def __contains__(self, name):
        return name in self._env

    def evaluate(self, variable):
        return self._env.evaluate(variable)

    def subst(self, value):
        return self._env.subst(value)
    
    def task(self, name=None, **kwargs):
        def wrapper(fn):
            self._env.task(
                self,
                fn,
                name if name is not None else fn.__name__.replace("_", "-"),
                **kwargs
            )

            return fn

        return wrapper

    def call(self, name, **vars):
        return self._env.call(name, **vars)

    def include(self, *patterns, **vars):
        for pattern in patterns:
            fullglob = os.path.join(
                os.path.dirname(self._filename),
                self._env.subst(pattern)
            )

        for entry in glob.glob(fullglob):
            self._env.include(entry, **vars)

    # Execute commands

    def capture(self, command, quite=None, abort=True, capture=STDOUT, retvals=(0,)):
        result = self.run(command, quite, abort, capture, retvals)
        return result.stdout

    def run(self, command, quite=None, abort=True, capture=NONE, retvals=(0,)):
        """ Run a command and return the results. """

        # Determine the shell to use
        shell = None
        if "TASKRUN_SHELL" in self:
            shell = self._env.evaluate("TASKRUN_SHELL")

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
            self._env.info(command)

        # Run the command
        try:
            stdout = stderr = None

            if capture & self.STDOUT or capture & self.STDERROUT:
                stdout = subprocess.PIPE

            if capture & self.STDERR:
                stderr = subprocess.PIPE
            elif capture & self.STDERROUT:
                stderr = subprocess.STDOUT

            process = subprocess.Popen(
                command,
                executable = shell,
                stdout=stdout,
                stderr=stderr,
                shell = True,
                env = shellenv
            )
            (stdout, stderr) = process.communicate()
            if process.returncode not in retvals and abort:
                raise CommandError("Unexpected return value")
        except OSError as e:
            # Error occurred starting the shell
            e = ShellError(shell)
            raise e
        except Exception as e:
            # Error occurred, try to print the file/line that called run
            e = CommandError(str(e))
            raise e

        return RunResult(stdout, stderr, process.returncode)


class Task(object):
    """ Represent a task to be called. """

    def __init__(self, script, fn, args):
        self._script = script
        self._env = script._env
        self._fn = fn
        self._vars = dict(args)

        self._called = False
        self._result = None
        
        self._once = bool(self._vars.pop("once", False))

    def execute(self, vars):
        if self._once and self._called:
            return self._result

        with self._env:
            self._env.update(**self._vars)
            self._env.update(**vars)

            self._result = self._fn()
            self._called = True

        return self._result

def realmain():

    # Create our environment
    e = Environment()

    # Walk up the paths to try to find the script file
    cwd = os.getcwd()

    curdir = cwd
    found = False
    while True:
        cmdfile = os.path.join(curdir, "TaskFile")
        if os.path.isfile(cmdfile):
            found = True
            break

        (head, tail) = os.path.split(curdir)
        if head and head != curdir:
            curdir = head
        else:
            break

    if not found:
        e.abort("Unable to find TaskFile")

    # Found the file, set up some variables
    e["TOP"] = curdir
    e["CWD"] = cwd

    # Load the script file
    e.include(cmdfile)

    # TODO: parse the command line for the command to execute


def main():
    try:
        realmain()
    except OSError as e:
        print("{0}: {1}".format(type(e).__name__, str(e)))




if __name__ == "__main__":
    main()

