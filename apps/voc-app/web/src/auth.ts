import { z } from "zod";

const sessionSchema = z
  .object({ email: z.string(), mode: z.enum(["local", "cognito"]) })
  .strict();
export type Session = z.infer<typeof sessionSchema>;

export const signInPath = "/auth/login";
export const sessionEndedEvent = "voc-session-ended";

/** Any request answered 401 means the session is over; the page shows sign-in. */
export function announceSessionEnded() {
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event(sessionEndedEvent));
}

export async function whoAmI(signal?: AbortSignal): Promise<Session | null> {
  const response = await fetch("/auth/me", {
    signal,
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("No pudimos comprobar la sesión.");
  return sessionSchema.parse(await response.json());
}

/** Ends the server session; the caller then navigates to the returned address. */
export async function signOut(): Promise<string> {
  const response = await fetch("/auth/logout", {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("No pudimos cerrar la sesión.");
  return z
    .object({ logout_url: z.string().min(1) })
    .parse(await response.json()).logout_url;
}

const loginErrors: Record<string, string> = {
  login_state_invalid:
    "El enlace de inicio de sesión caducó o ya se usó. Vuelve a iniciar sesión.",
  login_cancelled: "El inicio de sesión no se completó. Vuelve a intentarlo.",
  invalid_token: "No pudimos validar la sesión. Vuelve a iniciar sesión.",
  token_exchange_failed:
    "No pudimos completar el inicio de sesión. Vuelve a intentarlo.",
};

/** The server sends failed sign-ins back to "/?login_error=<code>". */
export function loginErrorMessage(search: string): string | null {
  const code = new URLSearchParams(search).get("login_error");
  if (!code) return null;
  return loginErrors[code] ?? "No pudimos iniciar sesión. Vuelve a intentarlo.";
}
