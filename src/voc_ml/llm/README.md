# LLM connections

Creates an OpenRouter client for LLM calls from notebooks and pipelines.
Creating the client does not send a request; you choose the model when requesting generation.

```python
from voc_ml.llm import create_openrouter_client

client = create_openrouter_client()  # Local construction; no model call.
# Later: client.chat.completions.create(model=..., messages=..., max_tokens=...)
```

Reads the key from `OPENROUTER_API_KEY` or the temporary session file.
See [credential setup](../../../config/README.md#optional-openrouter-access).

`topic_interpretation.py` implements workflow 01's structured feature proposal:
select bounded review evidence, request four JSON fields, validate cited review
IDs, and attach the result to a copy of the topic. Failed attempts retain their
responses and leave the topic evidence intact. This differs from the book's
short-label example. The notebook's explicit form controls paid requests; its
JSON inspection controls do not trigger them.
