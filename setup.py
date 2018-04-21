from setuptools import setup, find_packages

setup(
    name='mrbavii_taskrun',
    version='0.0',
    description='A simple python-based task runner',
    url='',
    author='Brian Allen Vanderburg II',
    license='MIT',
    packages=find_packages(),
    zip_safe=False,
    install_package_data=True,
    entry_points={
        'console_scripts': [
            'mrbavii-taskrun = mrbavii_taskrun.main:main'
        ]
    }
)
