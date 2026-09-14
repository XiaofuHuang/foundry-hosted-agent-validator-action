import dns from "node:dns/promises";
import fs from "node:fs/promises";
import https from "node:https";
import net from "node:net";
import path from "node:path";

const MAX_BYTES = 1024 * 1024;
const CONNECT_TIMEOUT_MS = 10_000;
const TRANSFER_TIMEOUT_MS = 30_000;

function ipv4ToNumber(address) {
  return address
    .split(".")
    .reduce((value, part) => ((value << 8) | Number(part)) >>> 0, 0);
}

function isInSubnet(value, base, prefix) {
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return ((value & mask) >>> 0) === ((ipv4ToNumber(base) & mask) >>> 0);
}

export function isPublicIPv4(address) {
  if (net.isIP(address) !== 4) {
    return false;
  }
  const value = ipv4ToNumber(address);
  return ![
    ["0.0.0.0", 8],
    ["10.0.0.0", 8],
    ["100.64.0.0", 10],
    ["127.0.0.0", 8],
    ["169.254.0.0", 16],
    ["172.16.0.0", 12],
    ["192.0.0.0", 24],
    ["192.0.2.0", 24],
    ["192.88.99.0", 24],
    ["192.168.0.0", 16],
    ["198.18.0.0", 15],
    ["198.51.100.0", 24],
    ["203.0.113.0", 24],
    ["224.0.0.0", 4],
    ["240.0.0.0", 4],
  ].some(([base, prefix]) => isInSubnet(value, base, prefix));
}

export function parseRemoteRulesUrl(value) {
  if (!/^[\x00-\x7f]+$/.test(value)) {
    throw new Error("Remote rules-file URL must be ASCII");
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("Remote rules-file must be a valid HTTPS URL");
  }
  if (url.protocol !== "https:" || !url.hostname) {
    throw new Error("Remote rules-file must use HTTPS");
  }
  if (url.username || url.password) {
    throw new Error("Remote rules-file URL must not contain credentials");
  }
  return url;
}

function requestRules(url, address) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let totalBytes = 0;
    let settled = false;
    let connectTimer;
    let transferTimer;

    const finish = (error, value) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(connectTimer);
      clearTimeout(transferTimer);
      error ? reject(error) : resolve(value);
    };
    const request = https.request(
      {
        hostname: url.hostname,
        port: url.port || 443,
        path: `${url.pathname}${url.search}`,
        headers: {
          Host: url.host,
          "User-Agent": "foundry-hosted-agent-validator-action",
        },
        agent: false,
        servername: url.hostname,
        lookup(_hostname, options, callback) {
          callback(
            null,
            options?.all ? [{ address, family: 4 }] : address,
            options?.all ? undefined : 4,
          );
        },
      },
      (response) => {
        if (response.statusCode !== 200) {
          response.resume();
          finish(new Error("Remote rules-file must return HTTP 200 without redirects"));
          return;
        }
        if (Number(response.headers["content-length"] || 0) > MAX_BYTES) {
          response.resume();
          finish(new Error("Remote rules-file must not exceed 1 MiB"));
          return;
        }
        response.on("data", (chunk) => {
          totalBytes += chunk.length;
          if (totalBytes > MAX_BYTES) {
            request.destroy(new Error("Remote rules-file must not exceed 1 MiB"));
          } else {
            chunks.push(chunk);
          }
        });
        response.on("end", () => finish(null, Buffer.concat(chunks)));
        response.on("error", (error) => finish(error));
      },
    );

    connectTimer = setTimeout(
      () => request.destroy(new Error("Remote rules-file connection timed out")),
      CONNECT_TIMEOUT_MS,
    );
    transferTimer = setTimeout(
      () => request.destroy(new Error("Remote rules-file transfer timed out")),
      TRANSFER_TIMEOUT_MS,
    );
    request.on("socket", (socket) => {
      if (socket.connecting) {
        socket.once("secureConnect", () => clearTimeout(connectTimer));
      } else {
        clearTimeout(connectTimer);
      }
    });
    request.on("error", (error) => finish(error));
    request.end();
  });
}

export async function downloadRemoteRules(value, destination) {
  const url = parseRemoteRulesUrl(value);
  const addresses = await dns.lookup(url.hostname, {
    family: 4,
    all: true,
    verbatim: true,
  });
  const address = addresses
    .map((entry) => entry.address)
    .filter(isPublicIPv4)
    .sort()[0];
  if (!address) {
    throw new Error("Remote rules-file host must resolve to a public IPv4 address");
  }

  const contents = await requestRules(url, address);
  await fs.mkdir(path.dirname(destination), { recursive: true });
  await fs.writeFile(destination, contents);
}
