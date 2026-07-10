# alfa — deepfake media pipeline workspace

This directory holds several sibling packages for a realtime deepfake media
demo/research pipeline. The current and planned components are described below.
Each package is its own git repository; the `alfa/` directory itself is a plain
workspace folder, not a repo.

## Overall pipeline

The media gateway receives realtime audio and video streams. Several independent
checks then produce score objects for RiskAPI:

- `deepfake-virtualcam-check` performs explainable virtual-camera, replay,
  signature, and liveness-signal checks;
- a separate ML service (not added to this workspace yet) will analyze both
  video and audio for deepfake signals;
- both services send their results to `deepfake-riskapi`, which stores check
  statuses and score objects in MongoDB.

RiskAPI is the result ingestion/storage boundary. It does not run the checks
itself and keeps the score payload flexible so each detector can submit its own
JSON object.

## deepfake-audio-video-inference

Headless realtime audio/video deepfake inference service (trimmed fork of RVC
WebUI). The laptop captures mic + webcam, sends both streams through a single
SSH-tunneled TCP connection to the cluster, and receives processed audio/video
back for local preview.

- **Stack:** Python 3.10, poetry, torch 2.4.0, fairseq, faiss-cpu. Video runs in
  a separate Deep-Live-Cam venv (`.venv_dlc`) with CUDA.
- **Primary runtime:** `backend.media_gateway.stream_server` (cluster) +
  `stream_client` (laptop) over SSH. This TCP stream path is the only runtime;
  the older UDP path was removed.
- **Key modules:** `backend/media_gateway/` — audio_engine (RVC), video_engine
  (Deep-Live-Cam adapter via subprocess), stream_server/client, and the
  signature glue (`stream_signature`, `signature_cli`, `signature_policy`,
  `signed_packets`). The wire protocol + framing live in the
  `deepfake-media-transport` sibling package (imported as
  `deepfake_media_transport`).
- **Cluster:** `ssh -i ~/.ssh/id_ed25519 -p 22010 master@62.183.4.208`,
  path `/home/master/work/alfa-deepfake/deepfake-audio-video-inference`. The `deepfake-media-transport`
  and `deepfake-stream-signature` sibling packages are deployed alongside it in
  `/home/master/work/alfa-deepfake/` and installed editable into its venv.
- **Runtime assets (not committed):** `assets/weights/voice_model.pth`,
  `assets/indices/voice_model.index`, `assets/hubert/hubert_base.pt`.
  Deep-Live-Cam expected at `~/workspace_w9line/deep_face/extracted/Deep-Live-Cam`.
- Full startup commands live in `deepfake-audio-video-inference/README.md` and
  `docs/en/media_gateway.md`.

## deepfake-media-transport

Standalone transport primitives for the media gateway, extracted so the wire
format lives in exactly one place instead of being copied between packages.

- **Stack:** Python 3.10+, `uv`, hatchling, zero runtime dependencies.
- **API:** `protocol` — `MAGIC`/`VERSION`, `StreamType`, `Codec`, `PacketHeader`,
  `MediaPacket`, `packetize_payload`, `PacketReassembler`; `framing` — length-
  prefixed TCP helpers `read_exact`, `read_frame`, `write_frame`,
  `iter_length_prefixed_packets`.
- **Consumers:** `deepfake-audio-video-inference` (stream server/client) and
  `deepfake-virtualcam-check` (gateway packet parsing) both import it.
- **Tests:** `./scripts/test.sh`.

## deepfake-stream-signature

Standalone, transport-agnostic C2PA-*like* stream signature primitives for the
media gateway demo. Defensive test layer only — it does NOT forge real C2PA
trust chains.

- **Stack:** Python 3.10+, `uv`, hatchling, zero runtime dependencies.
- **API:** `StreamSigner`, `StreamSignatureVerifier`, `StreamPacket`/
  `StreamPacketHeader`, `SignatureConfig`, `VerificationResult`.
- **Mechanics:** HMAC-SHA256 over canonical JSON manifest; payload bound via
  SHA-256; per-`(session_id, stream_type)` signature chaining; detects
  replay / tamper / invalid / untrusted-key / chain-mismatch. Envelope magic
  `DFS1`. Control stream (type 3) is never signed.
- **Tests:** `python3.10 -m unittest tests.test_stream_signature`.

## deepfake-virtualcam-check

Explainable CPU-side risk scoring for virtual cameras, replayed/frozen streams,
signature failures, suspicious timing/encoding, and optional active-liveness
signals. It can inspect media-gateway packets directly or run as a sampling TCP
proxy in front of the inference server. Its score object is intended to be sent
to RiskAPI.

## deepfake-riskapi

FastAPI/MongoDB service that receives check statuses and detector results. It
will accept results from both `deepfake-virtualcam-check` and the planned audio
and video ML service.

## Planned ML service

A separate service will inspect both audio and video with ML detectors and send
its result objects to `deepfake-riskapi`. It is part of the target architecture,
but no implementation directory exists in this workspace yet.

## Relationship between the two packages

`deepfake-audio-video-inference` and `deepfake-virtualcam-check` both consume the
`deepfake-stream-signature` and `deepfake-media-transport` sibling packages.
These are declared as editable path dependencies (Poetry `develop = true` /
uv `[tool.uv.sources]` `editable = true`) and installed into each package venv
with `uv pip install -e ../deepfake-media-transport -e ../deepfake-stream-signature`.
There is no `sys.path` bootstrapping anymore.

The signature layer inside audio-video-inference (`signature_policy.py`,
`signed_packets.py`, `signature_cli.py`, `stream_signature.py`) wires the
signature library into the media gateway with `--signature-policy off|log|block`.
`stream_signature.py` is a thin adapter from `deepfake_media_transport.MediaPacket`
to the signature library's transport-agnostic `StreamPacket`. The canonical
source of the signing/verification logic remains `deepfake-stream-signature`;
the canonical source of the wire protocol is `deepfake-media-transport`.

At the system level, stream signature verification and virtual-camera checks
provide non-ML evidence, while the planned ML service provides audio/video model
scores. RiskAPI is the common destination for their results.

## In-progress work (as of 2026-07-10)

Completed a global KISS/DRY refactor:

- Extracted the media-gateway wire protocol + framing into the new
  `deepfake-media-transport` package; removed the duplicate protocol copy that
  lived in `deepfake-virtualcam-check/gateway.py`.
- Removed the legacy UDP runtime from `deepfake-audio-video-inference`
  (`server.py`, `capture_client.py`, `preview_client.py`, `udp_tcp_tunnel.py`,
  `session.py`, `tools/udp_infer.py`, the `udp-*.sh` scripts, and
  `docs/en/udp_inference.md`). The TCP stream path is the only runtime.
- Replaced the `sys.path` sibling-import hack with real editable path
  dependencies in both consumer packages.
- Deleted the obsolete `udp-module/` prototype and the stray root git repo.

The changes are uncommitted in each package repo.
