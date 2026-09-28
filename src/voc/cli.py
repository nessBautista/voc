import click
from voc import __version__

@click.group()
def main():
    """VOC dataset tools."""

@main.command()
def version():
    click.echo(__version__)
