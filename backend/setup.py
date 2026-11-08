from setuptools import setup, find_packages

setup(
    name="cowork-agent",
    version="0.2.0",
    description="Self-hosted BYOK desktop agent that controls the mouse and keyboard.",
    packages=find_packages(),
    py_modules=["app", "config"],
    entry_points={
        "console_scripts": [
            "cowork=agent.cowork.cli:cli",
            "agent=agent.browser.cli:cli",
        ],
    },
)
