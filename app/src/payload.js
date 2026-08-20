const isPlainObject = (value) => {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
};

function canonicalValue(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Payload numbers must be finite.");
    return value;
  }
  if (Array.isArray(value)) return Array.from({length: value.length}, (_, index) => {
    if (!(index in value)) throw new Error("Payload contains an unsupported JSON value.");
    return canonicalValue(value[index]);
  });
  if (isPlainObject(value)) {
    return Object.keys(value).sort().reduce((out, key) => {
      out[key] = canonicalValue(value[key]);
      return out;
    }, {});
  }
  throw new Error("Payload contains an unsupported JSON value.");
}

export function canonicalizePayload(input) {
  if (typeof input === "string") return input;
  if (!Array.isArray(input) && !isPlainObject(input)) {
    throw new Error("Payload must be raw text or a JSON object/array.");
  }
  return JSON.stringify(canonicalValue(input));
}

export async function hashPayload(canonicalText) {
  if (typeof canonicalText !== "string") throw new Error("Payload hash input must be text.");
  if (!globalThis.crypto?.subtle) throw new Error("Web Crypto SHA-256 is unavailable in this browser.");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalText));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function toContractExpiry(localValue) {
  if (!localValue) throw new Error("Set an expiry time for this request.");
  const date = new Date(localValue);
  if (!Number.isFinite(date.getTime())) throw new Error("Expiry must be a valid date and time.");
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function defaultExpiryInput() {
  const date = new Date(Date.now() + 24 * 60 * 60 * 1000);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
