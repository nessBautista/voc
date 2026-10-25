compose := "sh scripts/workspace.sh"

# Load the selected credentials before Compose creates the container.
up:
    sh scripts/start.sh

down:
    {{compose}} down

shell:
    @{{compose}} shell

status:
    {{compose}} ps

test:
    {{compose}} exec workspace pytest -q

new-marimo $notebook_name:
    @{{compose}} notebook marimo create

new-notebook $notebook_name:
    @{{compose}} notebook jupyter create

marimo:
    @{{compose}} notebook marimo open

jupyter:
    @{{compose}} notebook jupyter open

jupyter-reports:
    @{{compose}} notebook jupyter reports

logs:
    {{compose}} exec workspace sh -c 'tail -n 60 /workspace/data/logs/*.log'
