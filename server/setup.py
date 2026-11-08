from setuptools import setup, find_packages

setup(
    name="open-computer-use",
    version="0.2.0",
    description="Self-hosted BYOK desktop agent that controls the mouse and keyboard.",
    packages=find_packages(),
    py_modules=["app", "config"],
    entry_points={
        "console_scripts": [
            "open-computer-use=agent.open_computer_use.cli:cli",
            "agent=agent.browser.cli:cli",
        ],
    },
)
