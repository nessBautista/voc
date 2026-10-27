# Inspecting VOC in LangSmith Studio

Run this on the **Mac**, from the VOC checkout:

```sh
just lang-studio
```

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and a LangSmith account. The first launch installs the locked development environment. Studio opens in the browser after the local Agent Server starts on `127.0.0.1:2024`. Leave the terminal open; **Ctrl+C** stops it. On macOS the launcher prefers Chrome when installed; Safari cannot connect to this localhost server from the hosted Studio page. No public tunnel is opened.

## Inspect the graph

Sign into LangSmith if prompted. Select the **voc** assistant and **Graph** view. You should see:

```text
interpret → resolve → query → compose → validate
                         ↓
                       assess → resolve (another Research step)
                         └────→ compose (finished)
```

This is the app's actual `build_graph`, with Daily and Research routes in one graph. It reads the local publication selected by `data/voc-app/state/active.json`. The startup output shows its publication ID. It does not connect to the EC2 server or show existing desktop conversations.

## Run a Daily question

In another Mac terminal, copy the generated input:

```sh
cd /Users/ness/workspace/voc
cat data/lang-studio/input-daily.json | pbcopy
```

Create a new Studio thread, switch its input editor to JSON, paste, and submit. Inspect each node's state and the final `answer`. The default **fixture** planner queries the real local publication without making model calls.

The complete input contains `question`, `mode`, `history`, `memory`, and `publication_id`. Keep these fields when editing the question. Studio exposes the graph's raw state; it does not run VOC's HTTP conversation manager. Its threads do not automatically assemble the app's four-turn planner history or topic memory. For independent questions, use a new thread with empty history and memory. Testing a follow-up requires supplying those fields explicitly.

## Run Research and step through it

Copy the Research input and submit it in a **new** Studio thread:

```sh
cat data/lang-studio/input-research.json | pbcopy
```

The input uses the demo's research question. Watch `assess` choose another query or finish. With the demo publication, the fixture run takes three steps. Set a breakpoint before `query` to inspect the resolved plan, then resume. The graph's query/step budgets remain unchanged; the local server also caps a run at 90 seconds. VOC's HTTP ownership, rate limiting, and per-mode outer timeouts are not exercised here.

## Optional model tests and LangSmith key

For the LangSmith API key described in the [Studio setup guide](https://docs.langchain.com/oss/python/langgraph/studio), or to test the model planner, create the private settings file once:

```sh
cp -n config/lang-studio.local.env.example config/lang-studio.local.env
open -e config/lang-studio.local.env
```

Set `LANGSMITH_API_KEY` to your key if Studio requires it for your workspace. The pinned local server also starts without a key when tracing is disabled.

For model tests, set `VOC_STUDIO_PLANNER_MODE=model` and `VOC_STUDIO_LLM_API_KEY` to your OpenRouter key in that file, then restart `just lang-studio`. These tests spend model-provider credit. Root app settings and credentials do not select Studio's planner mode. The launcher disables LangSmith trace uploads; seeing live AWS runs is a separate integration.

## Locations and controls

| Item | Location or command |
| --- | --- |
| Development dependencies | `tools/lang-studio/pyproject.toml` and `uv.lock`; separate from the deployed app |
| Studio threads/checkpoints | `tools/lang-studio/.langgraph_api/` (ignored by Git) |
| Generated test inputs | `data/lang-studio/input-daily.json` and `input-research.json` |
| Publication input overrides | `VOC_STUDIO_PUBLICATIONS_DIR` and `VOC_STUDIO_PUBLICATION_STATE_DIR` in the private file |
| Different port | `VOC_STUDIO_PORT=2025 just lang-studio` |
| Print the URL without opening a browser | `VOC_NO_BROWSER=1 just lang-studio` |

The catalog and app state are read through `PublicationService`; Studio never starts `Conversations`, `Reports`, or the app's login services. Keep the AWS tunnel on 8091 if you are presenting the demo at the same time. Restart Studio after changing its private settings or the selected publication to regenerate the example inputs.

If startup reports no active publication, restore your local demo publication first or set the two input paths above. Studio does not download or activate AWS data.

## If Studio says connection failed

When launched from the **Mac terminal**, Studio runs directly on the Mac; no Docker port forwarding is needed. Keep the launcher terminal open. In a second **Mac terminal**, check:

```sh
curl --fail http://127.0.0.1:2024/ok
```

Use your chosen port if you changed `VOC_STUDIO_PORT`.

- **Health returns `"ok": true`:** in Chrome, open the site controls beside the address bar for `https://smith.langchain.com`, then **Site settings**. Set **Loopback network → Allow** if shown separately; older versions use **Local network access → Allow**. Reload Studio. Apply the permission to `smith.langchain.com`, which makes the request, not to `127.0.0.1`. The Studio connection URL must be `http://127.0.0.1:2024`.
- **Health cannot connect:** return to the launcher terminal and check its first error. The server must stay running; fix that error before changing browser permissions or container networking.

An error saying permission was denied for the `loopback` address space means Chrome blocked access to the local server. Newer Chrome versions separate loopback from LAN access; changing server CORS headers does not grant this browser permission. See [Google Chrome’s permission guidance](https://github.com/GoogleChrome/modern-web-guidance/blob/main/skills/modern-web-guidance/guides/security/local-network-access.md).

If health works and the permission is already allowed, check Chrome DevTools → Console for the failed request and its error. See [Studio connection troubleshooting](https://docs.langchain.com/langsmith/troubleshooting-studio#chrome-connection-issues).


References: [Studio setup](https://docs.langchain.com/oss/python/langgraph/studio), [CLI configuration](https://docs.langchain.com/langsmith/cli), [graph implementation](../../apps/voc-app/server/src/voc_app/agent/graph.py).
