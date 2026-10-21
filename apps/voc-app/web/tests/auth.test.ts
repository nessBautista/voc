import { afterEach, expect, it, vi } from "vitest";
import {
  announceSessionEnded,
  loginErrorMessage,
  sessionEndedEvent,
  signOut,
  whoAmI,
} from "../src/auth";
import { DataError, loadDashboard } from "../src/api";

afterEach(() => vi.unstubAllGlobals());

it("reads the signed-in session and treats 401 as signed out", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ email: "a@example.com", mode: "cognito" }),
    ),
  );
  expect(await whoAmI()).toEqual({ email: "a@example.com", mode: "cognito" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ error: {} }, { status: 401 })),
  );
  expect(await whoAmI()).toBeNull();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ error: {} }, { status: 500 })),
  );
  await expect(whoAmI()).rejects.toThrow("comprobar la sesión");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ email: 1, mode: "cognito" })),
  );
  await expect(whoAmI()).rejects.toThrow();
});

it("signs out with a POST and hands back the provider's logout address", async () => {
  const calls: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      calls.push(init);
      return Response.json({ logout_url: "https://login.example/logout" });
    }),
  );
  expect(await signOut()).toBe("https://login.example/logout");
  expect(calls[0].method).toBe("POST");
});

it("explains failed sign-ins from the query string", () => {
  expect(loginErrorMessage("")).toBeNull();
  expect(loginErrorMessage("?login_error=login_cancelled")).toContain(
    "no se completó",
  );
  expect(loginErrorMessage("?login_error=something_else")).toContain(
    "No pudimos iniciar sesión",
  );
});

it("turns a 401 on data into a session_ended error and an event", async () => {
  const listener = vi.fn();
  vi.stubGlobal("window", { dispatchEvent: listener });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ error: {} }, { status: 401 })),
  );
  const failure = await loadDashboard(
    new AbortController().signal,
    vi.fn(),
  ).catch((error: unknown) => error);
  expect(failure).toBeInstanceOf(DataError);
  expect((failure as DataError).code).toBe("session_ended");
  expect(listener).toHaveBeenCalledTimes(1);
  expect((listener.mock.calls[0][0] as Event).type).toBe(sessionEndedEvent);
  announceSessionEnded();
  expect(listener).toHaveBeenCalledTimes(2);
});
