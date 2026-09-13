![firefly CLI](assets/banner.jpg)

# firefly CLI

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#install)

Generate images and video with Adobe Firefly from the command line.

Installs as the package `firefly-cli` and gives you a `firefly` command. Every command
takes `--json`, so you can pipe it into other tools.

```console
$ firefly image "a red fox asleep in deep snow, golden hour" --size 16:9
uploading nothing, submitting to gemini-flash
in_progress 40%
downloading 1/1
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Saved                                                                    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ C:\Users\you\.firefly\downloads\20260828-151204-a-red-fox-asleep-in.png  │
└──────────────────────────────────────────────────────────────────────────┘

$ firefly image "the same fox, but as a watercolour" --ref fox.png -n 2
$ firefly jobs
```

## How this works, and what it costs you

Adobe's supported Firefly API is sold under a separate enterprise contract. An
individual or Creative Cloud plan gets app credits but never an API key, so if you sign
in to Firefly with Google there is no sanctioned way to script it.

This CLI takes the other door. It opens the real `firefly.adobe.com` in a browser, you
sign in normally, and it keeps the headers the web app itself authenticates with. From
then on it replays the same calls the app makes, from your account, against your
credits.

Two consequences worth knowing before you install it:

- **This is not a supported Adobe interface.** Automating it is outside Adobe's terms of
  use, and the endpoints are undocumented, so they can change or disappear without
  notice. When that happens, `firefly discover` records what the app does now and
  `firefly config set` points the CLI at it.
- **Your token lives in `~/.firefly/config.json`.** Treat that file as a credential.

## Install

You do not need Python or anything else installed first.

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/Just-Bax/firefly-cli/master/install.ps1 | iex
```

**macOS / Linux**:

```bash
curl -fsSL https://raw.githubusercontent.com/Just-Bax/firefly-cli/master/install.sh | sh
```

Then open a **new** terminal and run `firefly login`.

The installer also fetches the Chromium build Playwright signs in with, about 150MB.
If that step is skipped or interrupted, `firefly setup` does it, and `firefly login`
fetches it itself rather than failing.

## Sign in

```console
$ firefly login
```

A browser opens on the Firefly page. Sign in, **then type any prompt and press Generate
once**. The window closes by itself as soon as it sees that call.

That generation is not optional. Adobe sends two anti-abuse headers,
`x-arp-session-id` and `x-nonce`, only with a real generation, and the generate endpoint
rejects everything without them. It does not say so: it answers `408 system under load`,
which looks like a transient outage and is not. A session captured from page load alone
looks perfectly healthy and works for nothing, so `firefly login` refuses to settle for
one. Use `--quick` to take just the token, which is only useful for renewing.

The browser profile is kept in `~/.firefly/browser`, and Adobe's own sign-in cookie
outlives the token by a wide margin, so renewal can happen headless without you.

### "This browser or app may not be secure"

Google refuses its sign-in page in a browser it can tell is automated. If you hit that,
the Adobe account itself is fine, only the Google step is blocked. Two ways past it.

**Sign in once in a browser Google trusts**, then point the CLI at it:

```console
$ firefly config set browser_channel msedge
$ firefly login
```

Any Chromium channel works: `msedge`, `chrome`, `chrome-beta`. Use `browser_path` for a
binary Playwright does not know by name, such as Brave. To reuse a profile you are
already signed in with, set `browser_profile` to its user-data directory, and close every
window of that browser first, since two processes cannot share one profile.

**Or skip the browser entirely** and paste a request. In your normal browser open
`firefly.adobe.com`, press F12, generate once, right-click the call to
`firefly-3p.ff.adobe.io/v2/3p-images/generate-async` in the Network tab, choose
**Copy > Copy as cURL**, then either paste it:

```console
$ firefly login --curl
<paste, then Ctrl-Z on Windows or Ctrl-D elsewhere>
```

or save it to a file and pass that:

```console
$ firefly login --curl request.txt
```

Copying a *generate* call rather than any other gets you the `x-arp-session-id`,
`x-nonce` and `x-account-id` headers, which no other request carries.

Pasted credentials cannot be renewed automatically, so you will need to re-paste when
they expire. The browser login is better wherever it works.

## Commands

| Command | Does |
|---|---|
| `firefly setup` | download the browser used for signing in |
| `firefly login` | sign in, by browser or `--curl` |
| `firefly whoami` | what is stored, and when it expires |
| `firefly image PROMPT` | generate an image |
| `firefly video PROMPT` | generate a video |
| `firefly jobs` | recent generations and where they were saved |
| `firefly status ID` | ask Adobe about one job |
| `firefly collect ID` | download a job submitted with `--no-wait` |
| `firefly cancel ID` | stop a running job |
| `firefly models` | models this CLI knows how to ask for |
| `firefly discover` | record what the web app calls, to fix a moved endpoint |
| `firefly raw` | call any endpoint directly |
| `firefly config` | show or change settings |

### Generating

A bare prompt is text-to-image. Adding `--ref` makes it image-to-image: the reference is
uploaded first and the prompt describes what to do with it.

```console
$ firefly image "a lighthouse in a storm" --size 16:9 -n 4
$ firefly image "make it winter" --ref lighthouse.png
$ firefly image "a logo mark, flat vector" --size 1024x1024 --seed 4242
```

Sizes are either an aspect (`1:1`, `4:3`, `3:4`, `16:9`, `9:16`) or explicit
`WIDTHxHEIGHT`. Files land in `~/.firefly/downloads` unless you pass `--out`.

### Models

`firefly models` reads Adobe's own catalogue, so it lists whatever your account can
reach today rather than what was known when this was written. Pass a Name to `--model`:

```console
$ firefly models --kind video
$ firefly video "..." --model veo:3.1-fast-generate
$ firefly image "..." --model flux:fluxPro
```

A bare family name picks that family's first enabled version, so `--model kling` works.
Image defaults to `gemini-flash:nano-banana-3`. Video has no default, because Adobe
offers forty of them and none is an obvious choice.

### Video

Video takes minutes rather than seconds, so `--no-wait` gets you a job id back
immediately:

```console
$ firefly video "a candle guttering in a draught" --model veo:3.1-fast-generate --no-wait
Submitted as 7f3a91c2.
Collect it with: firefly collect 7f3a91c2

$ firefly status 7f3a91c2
$ firefly collect 7f3a91c2
```

Video is silent unless you ask for sound. Adobe defaults `generateAudio` to false on
every model that offers it, so the prompt describing whooshes and shouts gets you
nothing without the flag:

```console
$ firefly video "a kung fu duel, whooshes and shouts" --model veo:3.1-fast-generate --audio
```

`firefly models --kind video` lists what your account can reach; `veo:3.1-generate`,
`veo:3.1-fast-generate` and the Kling O3 family are the ones that make sound.

## When Adobe moves something

A `404` from an endpoint that used to work means the web app has been redeployed. To
find out where it went:

```console
$ firefly discover image
```

A browser opens; generate once by hand. Every authenticated call is recorded to
`~/.firefly/capture` with the credentials stripped out, and the generation endpoint it
found is written to your settings. `firefly config show` will show the new value.

Another symptom worth naming: a persistent `408 system under load` on generate, while
other commands work, usually means the session is missing `x-arp-session-id` and
`x-nonce` rather than that Adobe is busy. `firefly whoami` shows whether they are set.

## Settings

```console
$ firefly config show
$ firefly config set download_dir D:\renders
$ firefly config set video_wait_seconds 1200
```

| Setting | Default | Means |
|---|---|---|
| `download_dir` | `~/.firefly/downloads` | where generated files are saved |
| `timeout_seconds` | 120 | per-request HTTP timeout |
| `poll_seconds` | 2.0 | how often a running job is checked |
| `image_wait_seconds` | 300 | give up waiting on an image after this |
| `video_wait_seconds` | 900 | give up waiting on a video after this |
| `max_retries` | 5 | retries when Adobe answers 408 "under load" |
| `watermark` | false | ask Adobe to watermark output |
| `browser_channel` | bundled Chromium | `msedge`, `chrome`, ... for sign-in |
| `browser_path` | - | an explicit browser binary, for Brave and friends |
| `browser_profile` | `~/.firefly/browser` | a user-data directory to sign in with |
| `image_path` / `video_path` | see `firefly models` | the endpoints, so a move can be fixed without a release |

## Scripting

Every command takes `--json`, and errors are JSON on stdout too:

```console
$ firefly image "a fox" --json | jq -r '.files[0]'
$ firefly image "" --json
{
  "error": "A prompt is required.",
  "exit_code": 1
}
```

Exit codes: `0` fine, `1` error, `2` bad usage, `3` not signed in, `4` not found,
`5` Adobe API error, `6` Adobe refused (content policy, or no credits).

To run against credentials other than the signed-in ones, set `FIREFLY_BEARER`, and
optionally `FIREFLY_API_KEY`, `FIREFLY_ACCOUNT_ID`, `FIREFLY_ARP_SESSION_ID` and
`FIREFLY_NONCE`. The environment wins over the stored file.

## Licence

MIT. Not affiliated with, endorsed by, or supported by Adobe.
