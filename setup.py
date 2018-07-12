from setuptools import setup

# https://python-packaging.readthedocs.io/en/latest/minimal.html
setup(
    author="Radon Rosborough",
    author_email="radon.neon@gmail.com",
    description="Mirror GNU ELPA packages to GitHub repositories.",
    license="MIT",
    install_requires=["PyGithub"],
    name="gnu-elpa-mirror",
    url="https://github.com/raxod502/gnu-elpa-mirror",
    version=None,
)
