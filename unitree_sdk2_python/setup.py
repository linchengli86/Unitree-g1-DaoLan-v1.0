"""DaoLan packaging adapter for the vendored SDK; upstream licenses unchanged."""
from setuptools import find_packages, setup

setup(name='unitree_sdk2py', version='1.0.1+daolan.snapshot',
      description='DaoLan packaging of vendored Unitree SDK2 Python sources',
      packages=find_packages(include=['unitree_sdk2py', 'unitree_sdk2py.*']),
      python_requires='>=3.8', install_requires=['cyclonedds==0.10.2', 'numpy'])
