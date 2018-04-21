Task Run(ner)
=============

Task Runner is a small personal script I developed out of my own needs to replace
GNU makefiles with something else. The general issue I had with make used to
automate various activities is detecting success or failure of a command and
acting accordingly instead of aborting

Task Runner does not track dependencies, it is not a build tool.  I allows for
various scenarios similar to make.  Tasks can be defined and executed via the
command line.  Variables can be passed to the entire script, or specific tasks.


Usage
=====

mrbavii-taskrun [-d dir] [-f file] [params] [tasks]

-d dir
    Specify the directory to search for the task file. By default walk
    up the directory tree until the task file is found

-f file
    Specify an alternative name for the task file.  By default the task file
    is expected to be named "TaskFile"

params: NAME=VALUE
    Specify main variables to be set when the task file is loaded

tasks: TASKNAME[:NAME=VALUE]*
    Specify tasks to be executed, optionally with task specific variables


Task File
=========

Once the task file is found, it is loaded and executed.  The main variables 
passed via command line will be set on the environment before the file is
executed.  Then for each task specified on the command line, the given task
from the file will be executed, optionally with any task-specific variables
set in the environment, overriding those from the task file or main variables.


Tasks
=====

A task is defined via a decorator.  When the task is executed, the state of
the variables is saved, so any environment variables assignments changed
during the task execution will be restored after.


Public API
==========

with env:
---------

The env object can be used in a context manager.  When done so, the state of
the variables will be saved and restored once the context exits.


env[name] = value
-----------------

Set a variable in the environment


env[name]
---------

Get a variable from the environment


name in env
-----------

Test if a variable is in the environment


env.update(**vars)
------------------

Set multiple variables in the environment


env.evaluate(name)
------------------

Evaluate a variable from the environment including any substitutions


env.subst(value)
----------------

Perform substitutions on a value.  Lists will have their values substituded,
dictionary value (but not keys) will be substituded, and strings will be
substituted.  Other values will be left as is.


env.escape(value)
-----------------

Escape a value so that substitution returns the orginal value.



@env.task(name=None, once=False, extend=False, **vars)
------------------------------------------------------

Declare the next function to be a task.  The function takes no arguments
and the return value of the function is ignored.

Parameters:
    name
        The name of the task. If not specified, use the function name
    once
        Specify the function to only be executed once even if the task
        is called more than once
    extend
        Each named task is actually a list of functions. By default env.task
        will raise an error if already defined. Setting this to try will add
        the function to the list in the named task.  The functions are executed
        in the order they are specified when the task is executed.  In addition
        each function's "once" parameter is unique to itself, so one function
        of a named task may have once set to True while another function of the
        same task may have once st to False.
    **vars
        Additional name=value parameters specify variables to be set when the
        task is called.  These will override variables set outside the task
        except for variables set via the command line.


env.calltask(name, **vars)
--------------------------

This can be used from within a task to call another task.

Parameters:
    name
        The name of the task to call
    **vars
        Variables to pass to the task


@env.func(name=None)
--------------------

Declare the next function to be a taskfile function.  This provides a way to
easily have the function exposed in other taskfiles.  The same could be done
via env variables, but they can be override via command line. The function
can take and return any arguments matching the the env.callfunc function.

Parameters:
    name
        The name of the taskfile function.  If not sepcified, use the function
        name.


env.callfunc(name, *args, **kwargs)
-----------------------------------

Call a taskfile function.

Parameters:
    name
        The name of the taskfile function to call
    *args - Positional arguments to pass
    **kwargs - Keyword arguments to pass
Returns:
    The return value of the taskfile function


env.include(*patterns)
----------------------


Include other files as part of the taskfile, relative to the current task file.
The patterns will have substitution applied and then used as a glob to match
the files to include.  Including a file does not perserve the environment, thus
variables can be set in another file to be included.


env.capture(command, quite=None, abort=True, capture=env.STDOUT, retvals=[0])
-----------------------------------------------------------------------------

Substitution is performed on the command and then the command is executed.
If quite is None, then the environment variable "TASKRUN_QUITE" is used,
otherwise the value is use, to determine if the command is printed before
running.  The output to capture can be specified, and the list of return
expected return values.  If an unexpected return value occurs, and abort is
True, then the script will abort.  Return the string of the captured output.

capture shold only be one of the values and not a combination.  If it is
env.STDERR, the the stderr will be returned, else the stdout.


env.run(command, quite=None, abort=True, capture=None, retvals=[0])
-------------------------------------------------------------------

The same as env.capture, excepte a RunResult object is returned
instead.  This object allows access to the stdout, stderr, and retcode
values.  Used in a bool context, the object is true if the return code
is within the expected retvals otherwise false.

capture may be a combination of

    env.STDOUT - Capture stdout
    env.STDERR - Capture stderr

or

    env.STDERROUT - Capture stdout and stderr on stdout


env.output(message)
env.outputln(message)
---------------------

Output a message to stdout with substitution, optionally with a newline.


env.error(message)
env.errorln(message)
--------------------

Output a message to stderr with substitution, optionally with a newline.


env.abort(message=None, retcode=-1)
-----------------------------------

Abort the script, optionally displaying a message to stderr with newline.


env.exit(retcode=0)
-------------------

Exit the script with the given return code.


Substitution
============

The syntax of a substitution is as follow
-----------------------------------------

$$
    Replace with a single "$"
$(NAME)
    Evaluate the given name with substitution (recursively)


Variables
=========

Variables of the form _NAME_ should not be used as they may be declared as
script variables in the future.

The following variables are significant to the script.
------------------------------------------------------

_TOP_
    Specify the TOP directory, ie the directory where the taskfile was found
ABSTOP
    The absolute path of the TOP directory
_CWD_
    Specify the current directory when the script is launched

_QUIET_
    If set, this can be true or false to have the default quiet value of
    env.run and env.capture.  This is evaluated with substitution

_SHELL_
    If set, specify the shell for the script to use for env.run and env.capture.
    This is evaluated with substitution

_SHELLENV_
    When env.run and env.capture are executed, the current OS environment is
    passed to the shell. This variable can specify additional OS environment
    variables to pass to the shell or override, as a dictionary.  This is
    evaluated with substitution.


Examples
========


env["_SHELL_"] = "/bin/bash"
env["_SHELLENV_"] = {"SHELLOPTS": "errexit:pipefail"}
env["_QUITE_"] = True
env["DATE"] = env.capture("date -u +%Y%m%d")

@env.task(once=True)
def welcome():
    env.outputln("Starting now...")

@env.task(extend=True)
def welcome():
    if "NAME" in env:
        env.outputln("Performing task...$(NAME)")


@env.task()
def backup():
    env.calltask("welcome", NAME="System Backup")
    if env.run("tar -cvf /backups/$(DATE).temp /etc", abort=False).retcode:
        env.run("rm /backups/$(DATE).temp")
    else:
        env.run("mv /backups/$(DATE).temp /backups/$(DATE).tar")

@env.task(extend=True)
def backup():
    env.calltask("welcome", NAME="Misc Backup")
    if env.run("tar -cvf /backups/$(DATE).temp /etc", abort=False).retcode:
        env.run("rm /backups/$(DATE).temp")
    else:
        env.run("mv /backups/$(DATE).temp /backups/$(DATE).tar")

env.include("tasks/*.tsk")

