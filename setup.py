#!/usr/bin/env python3

from setuptools import setup, find_namespace_packages

setup(
    name='mrbavii.taskrun',
    version='0.0.1',
    description='A simple python-based task runner',
    url='',
    author='Brian Allen Vanderburg II',
    packages=find_namespace_packages(),
    entry_points={
        'console_scripts': [
            'mrbavii-taskrun = mrbavii.taskrun.main:main'
        ]
    }
)
