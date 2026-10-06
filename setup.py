from setuptools import setup


setup(
    name="crate-dj",
    version="0.1.0",
    description="Local-first 7-inch vinyl selection assistant",
    python_requires=">=3.9",
    packages=["app"],
    package_data={"app": ["*.html", "*.json"]},
    entry_points={"console_scripts": ["crate=app.server:main"]},
)
