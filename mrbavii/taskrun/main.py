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

class Literal(object):
    """ Represent a literal value. """

    def __init__(self, value):
        self._value = value

    def __str__(self):
        return str(self._value)


class Default(object):
    """ Represent a default value that is set if not specified via command line.
        Note this only checks the top level variables not task variables.
    """

    def __init__(self, value):
        self._value = value


class Delete(object):
    """ A variable which, when assigned, will actually delete the variable. """

    def __init__(self):
        pass


class NoChange(object):
    """ A variable which, when assigned to the env, will not change the current variable value. """

    def __init__(self):
        pass


class Description(object):
    """ A variable which provides a description and value. """

    def __init__(self, desc, value=NoChange()):
        self._desc = desc
        self._value = value


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
        self._filters = {}
        self._modules = {}
        self._variables_default = set()
        self._variables = {}
        self._variable_stack = []
        self._script_stack = []
        self._verbose = []
        self._var_desc = {}

    def _load(self, filename):
        """ Load the filename. """
        with open(filename, "rU") as handle:
            code = handle.read()

        codeobj = compile(code, filename, "exec", dont_inherit=True)


        data = dict(self._get_script_globals())
        data["__file__"] = filename

        self._script_stack.append(filename)
        if "load" in self._verbose:
            self.errorln("Entering {0}".format(filename))
        exec(codeobj, data, data)
        if "load" in self._verbose:
            self.errorln("Leaving {0}".format(filename))
        self._script_stack.pop()

    def _get_script_globals(self):
        return {
            "env": self,
            "Error": Error,
            "Default": Default,
            "Literal": Literal,
            "Delete": Delete,
            "NoChange": NoChange,
            "Description": Description
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
        self.update(**vars)

    def pop(self):
        """ Restore the variable stack. """
        self._variables = self._variable_stack.pop()

    def __setitem__(self, name, value):
        """ Set a variable value. """
        if isinstance(value, Delete):
            # Delete the variable
            self._variables.pop(name, None)
            self._variables_default.discard(name)
        elif isinstance(value, Default):
            # Only set if not already set or if the set value was another default
            if name not in self._variables or name in self._variables_default:
                self[name] = value._value
                # direct set below clears default flag, so add it again
                self._variables_default.add(name)
        elif isinstance(value, NoChange):
            pass
        elif isinstance(value, Description):
            self._var_desc[name] = value._desc
            self[name] = value._value
        else:
            self._variables[name] = value
            # direct set clears the default flag
            self._variables_default.discard(name)

    def __getitem__(self, name):
        """ Get a variable value. """
        if name in self._variables:
            return self._variables[name]

        raise VariableError(name)

    def __contains__(self, name):
        """ Test for a variable. """
        return name in self._variables

    def update(self, **vars):
        """ Update variables of the environment. """
        for name in vars:
            # Use __setitem__ for all effects
            self[name] = vars[name]

    def evaluate(self, variable):
        """ Evaluate a variable. """
        return self.subst(self[variable])

    def subst(self, value, filter=None):
        """ Perform string substitution based on environment variables or escape values. """

        if isinstance(value, Literal):
            return value._value
        elif isinstance(value, tuple):
            return tuple(self.subst(i, filter) for i in value)
        elif isinstance(value, list):
            return list(self.subst(i, filter) for i in value)
        elif isinstance(value, dict):
            return {i: self.subst(value[i], filter) for i in value}
        elif isinstance(value, StringTypes):
            def subfn(mo):
                var = mo.group(0)

                if var == "$$":
                    return "$"

                # Apply variable filters
                parts = var[2:-1].split("|")
                value = self.evaluate(parts[0])

                if len(parts) > 1:
                    # Filters supplied directly
                    for part in parts[1:]:
                        if len(part) == 0:
                            # Empty filter can be used to disable auto filter
                            continue
                        else:
                            value = self.callfilter(part, value)
                elif filter:
                    # Use auto-filter if specified
                    for part in filter.split("|"):
                        value = self.callfilter(part, value)

                return value
            return re.sub(r"\$\$|\$\(.*?\)", subfn, value)
        else:
            return value

    def escape(self, value):
        """ Escape a string so subst will return the original value. """
        return re.sub(r"\$", "$$", value)

    def task(
            self, name=None, extend=False, once=True, depends=None, desc=None,
            **vars
        ):
        """ Decorator to register a task. """
        def wrapper(fn):
            if name is not None:
                _name = name
            else:
                _name = fn.__name__

            entries = self._tasks.setdefault(_name, [])
            if len(entries) and not extend:
                raise Error("Task already defined: {0}".format(_name))

            newtask = Task(self, fn, once, depends, desc, vars)
            entries.append(newtask)

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

            if _name in self._funcs:
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

    def filter(self, name=None):
        """ Decorator to register a filter. """
        def wrapper(fn):
            if name is not None:
                _name = name
            else:
                _name = fn.__name__

            if _name in self._filters:
                raise Error("Filter already defined: {0}".format(_name))

            self._filters[_name] = fn
            return fn
        return wrapper

    def callfilter(self, name, value):
        """ Call a filter with a value. """
        if name in self._filters:
            return self._filters[name](value)
        else:
            raise Error("No such filter: {0}".format(name))

    def module(self, name=None):
        """ Define a module factory. """
        def wrapper(fn):
            if name is not None:
                _name = name
            else:
                _name = fn.__name__

            if name in self._modules:
                raise Error("Module already defined: {0}".format(_name))

            self._modules[_name] = fn
            return fn
        return wrapper

    def loadmodule(self, name):
        """ Call the module factory. """

        if name in self._modules:
            return self._modules[name]()

        raise Error("No such module: {0}".format(name))

    def include(self, *patterns):
        """ Include a file. """
        for pattern in patterns:
            fullglob = os.path.join(
                os.path.dirname(self._script_stack[-1]),
                self.subst(pattern)
            )

            for entry in sorted(glob.glob(fullglob)):
                self._load(entry)

    def capture(self, command, quiet=None, abort=True, capture=STDOUT, retvals=(0,)):
        result = self.run(command, quiet, abort, capture, retvals)

        return result.stderr if capture == self.STDERR else result.stdout


    def run(self, command, quiet=None, abort=True, capture=NONE, retvals=(0,), filter=True):
        """ Run a command and return the results. """

        # Determine the shell to use
        shell = None
        if "_SHELL_" in self:
            shell = self.evaluate("_SHELL_")

        # Determine any changes to the shell environment
        shellenv = dict(os.environ)
        if "_SHELLENV_" in self:
            env = self.evaluate("_SHELLENV_")
            if isinstance(env, dict):
                for name in env:
                    shellenv[name] = env[name]

        # Determine if using a shell filter
        if filter and "_SHELLFILTER_" in self:
            filter = self.evaluate("_SHELLFILTER_")
        else:
            filter = None

        # Print the command if needed
        command = self.subst(command, filter=filter)

        if quiet is None and "_QUIET_" in self:
            quiet = bool(self.evaluate("_QUIET_"))

        if not quiet or "run" in self._verbose:
            # Make it literal since outputln also calls env.subst
            self.outputln(Literal(command))

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
        # Call twice instead of concat since message may be any subst-able value
        self.output(message)
        self.output("\n")

    def error(self, message):
        sys.stderr.write(self.subst(message))
        sys.stderr.flush()

    def errorln(self, message):
        # Call twice instead of concat since message may be any subst-able value
        self.error(message)
        self.error("\n")

    def abort(self, message=None, retcode=-1):
        if message is not None:
            self.errorln(message)
        self.exit(retcode)

    def exit(self, retcode=0):
        sys.exit(retcode)


class Task(object):
    """ Represent a task to be called. """

    def __init__(self, env, fn, once, depends, desc, args):
        self._env = env
        self._fn = fn

        self._once = once
        self._called = False

        if isinstance(depends, (tuple, list)):
            self._depends = tuple(depends)
        elif isinstance(depends, StringTypes):
            self._depends = (depends,)
        else:
            self._depends = ()

        if desc is not None:
            self._desc = desc
        elif fn.__doc__:
            self._desc = fn.__doc__.strip()

        self._vars = dict()
        self._var_desc = dict()
        for (name, value) in args.items():
            if isinstance(value, Description):
                self._vars[name] = value._value
                self._var_desc[name] = value._desc
            else:
                self._vars[name] = value


    def execute(self, vars):
        if self._once and self._called:
            return

        with self._env:
            # Update our vars first, then passed vars
            self._env.update(**self._vars)
            self._env.update(**vars)

            for depends in self._depends:
                self._env.calltask(depends)

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

        group = parser.add_mutually_exclusive_group(required=False)
        group.add_argument(
            "-d", "--dir", dest="dir", default=os.getcwd(),
            help="Specify a starting directory."
        )
        group.add_argument(
            "-s", "--system", dest="dir_system", action="store_true", default=False,
            help="Use the system taskfile directory."
        )
        group.add_argument(
            "-u", "--user", dest="dir_user", action="store_true", default=False,
            help="Use the user taskfile directory."
        )

        parser.add_argument(
            "-l", "--list", dest="list", default=False,
            action="store_true", help="List tasks."
        )
        parser.add_argument(
            "-w", "--walk", dest="walk", default=False, action="store_true",
            help="Wallk the directory tree to find the task file."
        )
        parser.add_argument(
            "-v", "--verbose", dest="verbose", default=[], action="append",
            choices=["load", "error", "run", "var"],
            help="Show verbose information."
        )
        parser.add_argument(
            "-V", dest="varhelp", default=False, action="store_true",
            help="Show variable-related help.")

        parser.add_argument(
            "-H", dest="taskhelp", default=False, action="store_true",
            help="Show task-related help.")

        parser.add_argument(
            "params", nargs="*",
            help="""Parameters in the form of <taskname>, <VAR>=<VALUE>, or
                  <taskname>:<VAR>=<VALUE>[<VAR>=<VALUE>...]"""
        )


        self.cmdline = parser.parse_args()

        # Process any needed arguments
        self.env._verbose = self.cmdline.verbose

        # TODO: need more system-specific way to do this, perhaps should
        # depend on and use code from mrbaviirc.common
        if self.cmdline.dir_user:
            self.cmdline.dir = os.path.expanduser("~/.config/mrbavii-taskrun")
        elif self.cmdline.dir_system:
            self.cmdline.dir = "/etc/mrbavii-taskrun"

    def find_taskfile(self):
        """ Find the task file. """
        filename = self.cmdline.file
        curdir = self.cmdline.dir

        if "load" in self.cmdline.verbose:
            self.env.errorln("Taskrun search directory: {0}".format(curdir))
            self.env.errorln("Taskrun search filename: {0}".format(filename))
            self.env.errorln("Taskrun walk path: {0}".format(str(self.cmdline.walk)))

        self.taskfile = None
        while True:
            taskfile = os.path.join(curdir, filename)
            if os.path.isfile(taskfile):
                if "load" in self.cmdline.verbose:
                    self.env.errorln("Task file found: {0}".format(taskfile))
                self.taskfile = taskfile
                return

            if not self.cmdline.walk:
                return

            (head, _) = os.path.split(curdir)
            if head and head != curdir:
                curdir = head
            else:
                break


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
                        if name[:1] == "_" or name[-1:] == "_":
                            raise Error("Setting special from command line not allowed")
                        taskparams[name] = value

                tasks.append((parts[0], taskparams))
            elif "=" in cmdparam:
                # NAME=VALUE
                (name, value) = cmdparam.split("=", 1)
                if name[:1] == "_" or name[-1:] == "_":
                    raise Error("Setting special _VARIABLES_ from command line not allowed")
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

        env["_TOP_"] = os.path.dirname(self.taskfile)
        env["_ABSTOP_"] = os.path.abspath(env["_TOP_"])
        env["_CWD_"] = os.path.abspath(self.cwd)
        env._load(self.taskfile)

        # Print tasks list if requested
        if self.cmdline.list:
            for name in env._tasks:
                if not name.startswith("_"):
                    env.outputln(name)
            env.exit()

        # Print var/task help if requested
        if self.cmdline.varhelp:
            self.show_varhelp()

        if self.cmdline.taskhelp:
            names = sorted(set([i[0] for i in tasks]))
            self.show_taskhelp(names)

        if self.cmdline.taskhelp or self.cmdline.varhelp:
            env.exit()

        # Execute the requested tasks setting task specific variables
        for (task, params) in tasks:
            env.calltask(task, **params)

    def show_varhelp(self):
        """ Show variable help. """

        env = self.env

        for var in sorted(env._var_desc):
            env.outputln("Var: {0}".format(var))
            env.outputln(" Desc: {0}".format(env._var_desc[var]))
            if "var" in self.cmdline.verbose:
                if var in env:
                    env.outputln(" Current Value: {0}".format(env[var]))
                else:
                    env.outputln(" Currently unset")

    def show_taskhelp(self, tasks):
        """ Show task help. """

        env = self.env

        if not tasks:
            tasks = sorted(env._tasks)

        for name in tasks:
            taskobj = env._tasks.get(name, None)
            if taskobj is None:
                raise Error("No such task: {0}".format(name))

            env.outputln("Task: {0}".format(name))
            for (entry_num, entry) in enumerate(taskobj):
                env.outputln(" Entry: {0}".format(entry_num))
                if entry._desc is not None:
                    env.outputln("  Desc: {0}".format(entry._desc))
                for var in entry._var_desc:
                    env.outputln("  Var: {0}".format(var))
                    env.outputln("   Desc: {0}".format(entry._var_desc[var]))
                    if "var" in self.cmdline.verbose:
                        if var in env:
                            env.outputln("   Current value: {0}".format(env[var]))
                        else:
                            env.outputln("   Currently unset")

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
            self.dump_errors(type, value, tb, False)


    def dump_errors(self, type, value, tb, all):
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

            show = True
            if not all and not "error" in self.cmdline.verbose and "mrbavii_taskrun" in fname and "main.py" in fname:
                show = False


            if show:
                env.errorln("  {0}:{1}".format(fname, lineno))

        env.abort("Aborting due to errors.")


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()

