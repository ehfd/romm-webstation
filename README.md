# romm-webstation

All in one emulation desktop for [RomM](https://romm.app/), streamed to the
browser with [selkies](https://docs.linuxserver.io/selkies/) and driven by
[romm-broker](https://github.com/romm-streaming/romm-broker). RomM activates a
game over REST, the broker launches the right emulator inside this container,
restores save data, and hands back a link to a collaborative room with the
stream embedded in it.

The image is built on
[linuxserver.io's selkies base image](https://github.com/linuxserver/docker-baseimage-selkies)
and is the continuation of the `romm` branch of
[linuxserver/docker-webstation](https://github.com/linuxserver/docker-webstation).

**Full documentation, including RomM wiring, reverse proxy, GPU setup and
per emulator BIOS notes: https://romm-streaming.github.io/romm-broker/**

```
ghcr.io/romm-streaming/romm-webstation
```

## Tags

| Tag | What it is |
| --- | --- |
| `latest` | The newest stable romm-broker release, rebuilt weekly for package updates. |
| `vX.Y.Z` | That broker release. Moves on weekly package rebuilds until the next release is cut. |
| `vX.Y.Z-<pkg>` | Immutable. One exact build of that release with that package set. |
| `dev` | The head of romm-broker's `master` branch, rebuilt on every change. |
| `dev-<sha>-<pkg>` | Immutable. One exact build of that broker commit with that package set. |

`<pkg>` is the first eight characters of the md5 of
[`package_versions.txt`](package_versions.txt), a listing of every apt, pip
and npm package in the image plus the version each emulator resolved to at
build time. A weekly rebuild that changes nothing produces no new tag.

## What is inside

The desktop ships every emulator the broker can drive, with controller
profiles and sane defaults pre-seeded on first run:

Azahar, Cemu, Dolphin, DOSBox Staging, DuckStation, Eden, ES-DE, Flycast,
GZDoom and Freedoom, MAME, melonDS, Modrinth App, PCSX2, PPSSPP, RetroArch,
RPCS3, ScummVM, shadPS4, xemu, Xenia Edge, plus DarkPlaces, EDuke32, Flips,
a Chromium browser and a file manager.

Dolphin, Eden and Cemu are compiled from source at build time; the rest
install from Ubuntu PPAs, upstream AppImages or release tarballs. Inside a
running container `webstation-versions` prints what each one resolved to.

## Running it

The image only runs in Wayland mode and is meant to be used with a GPU.
Without one the desktop still works on software rendering but most emulators
will not. The [container page](https://romm-streaming.github.io/romm-broker/docs/container)
on the docs site has the full parameter list, the NVIDIA notes and the RomM
`config.yml` to pair with it.

```yaml
services:
  webstation:
    image: ghcr.io/romm-streaming/romm-webstation:latest
    container_name: webstation
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - SUBFOLDER=/streaming/
      - BROKER_SECRET=change-me
    devices:
      - /dev/dri:/dev/dri
    volumes:
      - /path/to/config:/config
      - /path/to/library:/romm/library
    ports:
      - 3000:3000
      - 3001:3001
    shm_size: "1gb"
    restart: unless-stopped
```

| Parameter | Function |
| --- | --- |
| `-p 3000` | HTTP. Must sit behind a reverse proxy. |
| `-p 3001` | HTTPS with a self signed certificate, for direct access while testing. |
| `-v /config` | The user's home. Emulator settings, BIOS and firmware, save data, the broker's pid record and export directory. |
| `-v /romm/library` | Your ROM library, at the same path RomM mounts it. |
| `-e PUID` / `-e PGID` | The uid and gid the desktop, emulators and broker run as. |
| `-e SUBFOLDER` | URL prefix everything is served under. Default `/streaming/`, must match your reverse proxy. |
| `-e BROKER_SECRET` | Shared secret RomM sends as `X-Broker-Secret`. Without it the broker refuses to start. |
| `--shm-size=1gb` | Recommended for every desktop image. |

Open `https://yourhost:3001/streaming/` once to run each emulator's first time
setup, then wire the container into RomM as described in the docs.

## How this image is built

Everything runs on GitHub hosted runners under `.github/workflows/`. There
are no self hosted builders and no personal access tokens to rotate: every
workflow uses the repository's own `GITHUB_TOKEN`.

### The pipeline

`build.yml` is the only workflow that touches the image, and it runs the
same way whether a push to this repository, a broker release or commit, the
weekly check or a pull request asked for it.

1. **Build** on one runner. The runner first reclaims disk with
   `ci/scripts/free-disk.sh`, then builds the Dockerfile and pushes the
   result to GHCR **by digest only**, with no tag. Nothing a user can pull
   changes yet. Dolphin, Eden and Cemu are the exception: each is compiled
   once per upstream version into
   `ghcr.io/romm-streaming/romm-webstation-emulators` and copied in from
   there, so only a new emulator release pays for a compile.
2. **Test** on a second runner. It pulls that digest, generates
   `package_versions.txt` for it with syft plus the in image versions
   manifest, runs the smoke suite under `ci/` against a live container, and
   runs a report only vulnerability scan on the SBOM. Screenshots, logs and
   the package listing are uploaded as the `smoke-artifacts` download.
3. **Publish** on a third runner, only if the tests passed. It points the real
   tags at the tested digest with `docker buildx imagetools create`, which
   is a manifest operation and uploads nothing. On stable builds it then
   commits the new `package_versions.txt` to the default branch as the
   `romm-streaming-ci` GitHub App, the only actor allowed past `main`'s
   ruleset. `push.yml` ignores that file, so the commit does not rebuild
   the image it describes. These commits also keep the repository active,
   which stops GitHub disabling its scheduled workflows after 60 idle days.
4. **Cleanup** deletes the untagged digest when a candidate passed its tests
   but was deliberately not published: a dry run, a pull request, or a weekly
   rebuild that changed nothing. A candidate that **failed** its tests is left
   in place, untagged, so it can be pulled by digest and debugged; the test
   summary prints the command. Delete those by hand from the package's
   versions page once you are done with them.

A stable build whose package hash matches what is already recorded is not
published at all. That is how the weekly rebuild avoids churning tags when
nothing moved.

The publish job pushes directly to the default branch. If branch protection
is turned on later it needs to allow the `github-actions[bot]` pushes, or the
record step will fail while the tags have already moved.

### Triggers

| Workflow | When | What |
| --- | --- | --- |
| `push.yml` | every push to `main` | Rebuilds and publishes both `latest` and `dev`, whether or not the package hash moved, because the Dockerfile or overlay changed. Documentation only changes and the bot's own `package_versions.txt` commits do not trigger it. |
| `check-upstream.yml` | every fifteen minutes | Reads romm-broker's latest release and `master` head, compares each with the `broker-ref` label on `:latest` and `:dev`, and dispatches a build for whichever differs. Skips a channel that already has a build queued or running. |
| `package-check.yml` | weekly, Sunday morning UTC | Dispatches a stable build of the current release so base image, apt and emulator updates get picked up. |
| `pr.yml` | pull requests | Lints the shell, workflow and Python pieces, then runs the full build and smoke suite with publishing off. Fork PRs get the lint job only, because a fork's token cannot push the candidate for the test runner to pull. |
| `build.yml` | by hand | `Actions > Build image > Run workflow`. Pick the channel, give a release tag or commit sha, and turn publish off for a dry run. |

Polling is what lets this repository live anywhere without a token from the
broker repository. `check-upstream.yml` also accepts a `repository_dispatch`
of type `broker-release` or `broker-push`, so romm-broker's own workflows
can trigger it immediately later if that is ever wanted; the poll stays as
the fallback.

Every stable release gets built. For `master` the poll builds whatever HEAD
is at the time it looks, so a burst of commits inside one window produces
one dev image, and `dev` always ends up at the newest commit.

### Runner disk

The image is about ten gigabytes uncompressed and the compiled emulators
need several more during the build. A GitHub hosted runner guarantees about
fourteen gigabytes free on its root disk, which is why the build and the
tests run on separate runners and why every job starts with
`ci/scripts/free-disk.sh`: it removes preinstalled toolchains nothing here
uses and, when the runner's second disk at `/mnt` has more room, moves
docker's data root there. The script prints `df` before and after so each
run log shows what it actually had to work with.

### Smoke tests

`ci/` is a pytest suite that treats the image as a black box. It starts a
container with a known `BROKER_SECRET` and no GPU, exactly what a runner can
offer, and checks:

- **Boot.** Health answers, every s6 service the overlay and base image
  define is active, the broker started exactly once, every launcher shipped
  in `/defaults/desktop` points at a binary that exists, and the versions
  manifest covers every emulator resolved from an upstream "latest".
- **Auth.** Every lifecycle route answers 403 without the secret or with a
  wrong one. The room context answers 401 for a bogus token. The selkies
  token endpoint, which mints stream tokens, is closed. The stream and room
  websockets refuse connections with no token or a bogus one.
- **Desktop session.** Activate a `desktop` session through the API, confirm
  status reports it, confirm the stream websocket accepts the session token
  and closes a bogus one, open the room URL in headless Chromium, wait for
  selkies to report the stream started, screenshot it, and check the image
  is a picture rather than a blank canvas. Then exit and confirm the session
  is gone. The screenshot is kept as an artifact.
- **Security posture.** The broker, selkies, the compositor and PulseAudio
  run as `abc`, the broker and selkies ports are bound to loopback only with
  nginx the only public listener, dev mode is off, the selkies master token
  and the broker secret appear in no response or log, and a container
  started without `BROKER_SECRET` refuses to bring the broker up.

Run it against any image on a machine with docker:

```bash
ci/run-local.sh ghcr.io/romm-streaming/romm-webstation:latest
ci/run-local.sh romm-webstation:local -k desktop -v
WEBSTATION_KEEP=1 ci/run-local.sh romm-webstation:local   # leave the container up afterwards
```

Screenshots and container logs land in `ci/artifacts/`. Adding a check means
adding a test file next to the others; anything importable from
`conftest.py` gives you a running container, an HTTPS client with the
secret, and `docker exec` inside it.

### Building locally

```bash
docker build \
  --build-arg BROKER_RELEASE=v0.10.0 \
  --build-arg VERSION=v0.10.0 \
  --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t romm-webstation:local .
ci/scripts/package-versions.sh romm-webstation:local package_versions.txt
ci/run-local.sh romm-webstation:local
```

`BROKER_RELEASE` takes a release tag or any commit sha from romm-broker. Left
unset, the Dockerfile picks the latest release itself.

## Repository layout

```
Dockerfile               the image, single file, builder stages for Dolphin, Eden and Cemu
root/                    files layered over the base image: s6 services, nginx template,
                         emulator defaults, desktop launchers, the MOTD branding
package_versions.txt     what the current :latest contains, committed by the pipeline
ci/                      smoke tests and the scripts the workflows call
.github/workflows/       build.yml, push.yml, check-upstream.yml, package-check.yml, pr.yml
```

## License

[GPL-3.0](LICENSE), carried over from docker-webstation. The broker itself is
[AGPL-3.0](https://github.com/romm-streaming/romm-broker/blob/master/LICENSE).
