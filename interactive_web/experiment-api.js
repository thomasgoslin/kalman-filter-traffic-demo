export const cloudflareApi = document.documentElement.hasAttribute("data-cloudflare")
  ? import("./cloudflare-api.js")
  : null;

export async function request(path, options) {
  if (cloudflareApi) return (await cloudflareApi).request(path, options);
  const response = await fetch(path, options);
  if (!(response.headers.get("Content-Type") ?? "").includes("application/json")) {
    throw new Error("Start the Traffic Lab with its launcher to run experiments.");
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.message);
  return body;
}
