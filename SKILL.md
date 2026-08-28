---
name: firefly
description: Generate images and video with Adobe Firefly through the `firefly` command line client. Use when the user asks to generate, create, make or render an image, picture, illustration, logo, mockup, background or video, or to restyle or edit an image they already have. Triggers on "generate an image", "make me a picture", "draw", "render", "Firefly", "Adobe Firefly", "text to image", "image to image", "text to video".
---

# firefly

Command line client for Adobe Firefly. It drives the endpoints `firefly.adobe.com` uses,
with the user's own signed-in account and their own credits.

## Parse `--json`, and put the flag last

```bash
firefly image "a fox" --json      # works
firefly --json image "a fox"      # exit 2, unrecognized argument
```

Errors are JSON on stdout too: `{"error": "...", "exit_code": 3}`.

Exit codes: `0` fine, `1` error, `2` bad usage, `3` not signed in, `4` not found,
`5` Adobe API error, `6` Adobe refused (content policy, or no credits).

## Generating

```bash
firefly image "a red fox asleep in deep snow, golden hour" --size 16:9 --json
firefly image "the same fox as a watercolour" --ref /path/to/fox.png --json
firefly video "a candle guttering in a draught" --no-wait --json
```

A bare prompt is text-to-image. **Adding `--ref` makes it image-to-image**: the file is
uploaded first and the prompt describes what to do with it. `--ref` is repeatable and
takes a path to a local file, never a URL.

`--size` is an aspect (`1:1`, `4:3`, `3:4`, `16:9`, `9:16`) or explicit `WIDTHxHEIGHT`.
`-n` asks for several variations of one prompt (image only). `--seed` makes a generation
reproducible.

Files are written to `~/.firefly/downloads` unless `--out DIR` says otherwise. The JSON
result carries their absolute paths in `files`:

```json
{"job_id": "abc123", "kind": "image", "status": "SUCCEEDED", "files": ["/home/you/.firefly/downloads/20260828-151204-a-red-fox.png"]}
```

Read that path to look at the result. Do not try to fetch the Adobe URL yourself: the
presigned links expire and are already downloaded by the time the command returns.

## Video is slow, and needs a model

Video takes minutes, not seconds. Use `--no-wait`, then poll:

```bash
firefly models --kind video --json
firefly video "..." --model veo:3.1-fast-generate --no-wait --json
firefly status abc123 --json
firefly collect abc123 --json          # downloads once it is done
```

**`--model` is required for video.** Adobe offers around forty and none is a sensible
default, so `firefly video` without one errors and tells you to list them. For images
the default is fine; only pass `--model` if the user asks for a particular one.

**Video is silent without `--audio`.** Adobe defaults audio off on every model that has
it, so prompt wording alone gets a mute file. Pass the flag whenever the user asks for
sound, dialogue or effects; Veo and Kling O3 support it, Luma and Runway do not.

`firefly models` is read live from Adobe and cached for a day, so it is the truth about
what is available, not this file.

## Traps

- **A prompt is one argument.** Quote it. `firefly image a red fox` fails on usage.
- **Never put a URL in `--ref`.** Download it to a file first.
- **`-n` is image only.** `firefly video ... -n 2` is a usage error.
- **Audio is opt-in.** Describing sound in the prompt does nothing on its own.
- **Exit code 6 is not a bug.** It means Adobe refused the prompt on content policy, or
  the account is out of credits. Say which, from the error text, rather than retrying.
- **Exit code 3 means the user must act.** Tell them to run `firefly login`; it opens a
  browser and needs them to press Generate once, so it cannot be automated.
- **A 404 means Adobe moved the endpoint.** Point the user at `firefly discover`.
- **A repeated "system under load" is not load.** Adobe returns 408 for a session that
  lacks `x-arp-session-id` and `x-nonce`. Check `firefly whoami --json`: if either is
  null, tell the user to run `firefly login` again and press Generate during it. Do not
  just retry.

## Checking state

```bash
firefly whoami --json     # signed in? when does the token expire?
firefly jobs --json       # recent generations and where they were saved
firefly models --json     # what --model accepts
firefly config show --json
```

`whoami` and `config show` never print the token.
