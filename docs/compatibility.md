# Runtime and gateway compatibility

The plugin only creates a request header. It does not create routing affinity by
itself: the receiving gateway, proxy, or runtime must document and implement the
header's semantics.

These notes were rechecked against upstream documentation on 2026-07-15. Olla
through Thunder Forge is the tested recipe; the other entries are compatibility
guidance and should be verified against a real deployment before use.

## Generic gateway recipe

Use the exact canonical identity of a named Hermes custom provider and the
header consumed by its fronting gateway:

```yaml
plugins:
  entries:
    hermes-custom-header-plugin:
      providers:
        custom:my-local-inference:
          headers:
            X-Session-ID:
              strategy: hmac-sha256
              namespace: local-instance-a
              inputs:
                - session_id
                - model
              prefix: hermes-
              digest_length: 32
```

The provider must have a unique normalized URL in Hermes configuration. Store a
unique `HERMES_CUSTOM_HEADER_HMAC_KEY` for this installation in
`~/.hermes/.env`.

## Olla

[Olla](https://github.com/thushan/olla) implements sticky sessions and can use
an explicit session header such as `X-Olla-Session-ID`. Configure
`session_header` among its key sources and verify that every proxy preserves the
header. Olla owns backend selection, repinning, TTL, and observability; this
plugin only provides the stable opaque key.

## oMLX

[oMLX](https://github.com/jundot/omlx) exposes an OpenAI-compatible API and has
an upstream Hermes integration. A direct single-server deployment does not need
backend affinity, and its reviewed documentation does not define a
session-affinity request header. Use this plugin only when an external router in
front of multiple oMLX servers consumes a documented header.

## llama.cpp

[llama.cpp](https://github.com/ggml-org/llama.cpp) provides OpenAI-compatible
endpoints through `llama-server`, parallel slots, prompt-similarity reuse, and a
multi-model router. Its current
[server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
does not define a request header for session-to-slot or backend affinity. Put
the header contract in an external load balancer or gateway instead.

## Ollama

[Ollama](https://github.com/ollama/ollama) documents OpenAI-compatible chat and
responses endpoints, but its
[compatibility guide](https://docs.ollama.com/api/openai-compatibility) does not
define header-based affinity. `keep_alive` is a request-body option for model
residency and is outside this header-only plugin. Use a rule only when an
external Ollama gateway documents the header it consumes.

## LiteLLM

[LiteLLM](https://github.com/BerriAI/litellm) can forward client `x-*` headers
to backend model calls when `forward_client_headers_to_llm_api` is enabled, but
the stock router does not become sticky merely because it receives
`X-Session-ID`. Use this plugin with a downstream router or custom LiteLLM hook
that defines the semantics. Review the full forwarded-header surface before
enabling broad client-header forwarding.

Unknown headers may be ignored or rejected. Always test the complete path and
keep raw session keys, provider credentials, endpoints, and HMAC secrets out of
published evidence.
