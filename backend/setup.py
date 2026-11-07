from setuptools import setup, find_packages

setup(
    name="browser-agent",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "agent=agent.browser.cli:cli",
        ],
    },
)
