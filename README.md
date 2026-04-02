<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/header.svg">
  <img alt="S4F3 R3L4Y — Open-source MCP servers by Itasha Corp" src=".github/assets/header.svg" width="100%">
</picture>

*Signal relay infrastructure for the Wired. Every protocol finds its route.*

[![Python](https://img.shields.io/badge/python-3.10+-00FFFF.svg?style=flat-square)](https://www.python.org/downloads/)
[![Node.js](https://img.shields.io/badge/node.js-18+-00FFFF.svg?style=flat-square)](https://nodejs.org/)
[![MCP](https://img.shields.io/badge/MCP-servers-FF00FF.svg?style=flat-square)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/license-Apache_2.0-00FFFF.svg?style=flat-square)](LICENSE)
[![Open Source](https://img.shields.io/badge/open_source-01fe36.svg?style=flat-square)](#-contributing)

---

[**SERVERS**](#-servers) · [**INSTALL**](#-installation) · [**USAGE**](#-usage) · [**ARCHITECTURE**](#-architecture) · [**CONTRIBUTING**](#-contributing)

</div>

---

## > Overview

**S4F3 R3L4Y** is a collection of open-source [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) servers built by **Itasha Corp**. These servers relay context and tools between AI systems and external services — GitHub, ComfyUI, and MCP infrastructure management.

Each server is independently installable and production-tested.

```
  AI client (Claude, etc.)              external services
  ┌─────────────────────┐    MCP       ┌──────────────────┐
  │  coding assistant    │ ◄─────────► │  GitHub API       │
  │  image pipeline      │   relay     │  ComfyUI server   │
  │  tool orchestrator   │ ◄─────────► │  MCP registry     │
  └─────────────────────┘              └──────────────────┘
```

---

## > Servers

### GitHub Native

Lightweight MCP server wrapping the `gh` CLI. No Docker required.

| Tool | Description |
|------|-------------|
| `create_issue` | Create GitHub issues |
| `list_issues` | List and filter issues |
| `get_issue` | Get issue details |
| `create_pull_request` | Create PRs |
| `list_pull_requests` | List and filter PRs |
| `get_file_contents` | Read repository files |
| `search_repositories` | Search repos by query |
| `search_code` | Search code across repos |

**Stack**: Node.js, `@modelcontextprotocol/sdk`
**Requires**: `gh` CLI authenticated

### ComfyUI MCP Server

Comprehensive MCP server for ComfyUI image generation with 36+ tools.

| Category | Tools |
|----------|-------|
| Generation | txt2img, img2img, inpainting |
| Models | Discovery, loading, architecture detection |
| Queue | Job submission, monitoring, cancellation |
| Monitoring | VRAM usage, model-specific thresholds |
| Workflows | Template management, variable rendering |
| Nodes | Schema validation, fuzzy matching |

**Stack**: Python, FastMCP, httpx, websockets
**Requires**: Running ComfyUI instance

### MCP Management

Infrastructure tools for managing multiple MCP servers.

| Tool | Description |
|------|-------------|
| Tool Catalog | Discover and catalog all available MCP tools |
| Loading Strategy | Smart lazy-loading and initialization |
| Schema Extractor | Extract tool schemas from running servers |
| Health Monitor | Health checks for lazy-loaded servers |
| Agent Preloader | Preloading strategies for agent startup |
| Server Sync | Configuration synchronization |

**Stack**: Python, PyYAML

---

## > Installation

### GitHub Native Server

```bash
cd servers/github-native
npm install
```

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "github-native": {
      "command": "node",
      "args": ["path/to/servers/github-native/index.js"]
    }
  }
}
```

### ComfyUI Server

```bash
pip install fastmcp httpx websockets rapidfuzz
```

```json
{
  "mcpServers": {
    "comfyui": {
      "command": "python",
      "args": ["-m", "servers.comfyui.server"],
      "env": {
        "COMFYUI_URL": "http://localhost:8188"
      }
    }
  }
}
```

### Management Tools

```bash
pip install pyyaml
```

---

## > Usage

### GitHub Native

```bash
# Standalone test
node servers/github-native/index.js

# The server exposes tools via MCP protocol — connect via any MCP client
```

### ComfyUI

```python
# Direct usage (outside MCP)
from servers.comfyui.client import ComfyUIClient

client = ComfyUIClient("http://localhost:8188")
result = await client.txt2img(prompt="a quiet node in the network", steps=20)
```

### Management

```bash
# Discover all tools from running MCP servers
python -m management --discover

# Catalog tools
python -m management --catalog
```

---

## > Architecture

```
s4f3-relay/
├── servers/
│   ├── github-native/      # Node.js — gh CLI wrapper
│   │   ├── index.js        # MCP server entry point
│   │   └── package.json    # Node dependencies
│   └── comfyui/            # Python — FastMCP server
│       ├── server.py        # 36+ tool definitions
│       ├── client.py        # ComfyUI REST + WebSocket client
│       ├── types.py         # Data models
│       └── validator.py     # Node schema validation
├── management/             # Python — MCP infrastructure
│   ├── mcp_tool_catalog.py  # Tool discovery and indexing
│   ├── mcp_loading_strategy.py  # Smart loading
│   └── mcp_schema_extractor.py  # Schema extraction
├── pyproject.toml          # Python package config
└── LICENSE                 # Apache 2.0
```

---

## > Contributing

Contributions welcome. Please:

1. Fork the repository
2. Create a feature branch (`feat/your-feature`)
3. Write tests for new functionality
4. Ensure all existing tests pass
5. Submit a PR with a clear description

### Development

```bash
git clone https://github.com/46b-ETYKiAL/Itasha.Corp_S4F3-R3L4Y.git
cd Itasha.Corp_S4F3-R3L4Y
pip install -e ".[dev]"
```

---

## > Related

| Repo | Description |
|------|-------------|
| [S4F3 R0UT3 4RB1T3R](https://github.com/46b-ETYKiAL/Itasha.Corp_S4F3-R0UT3-4RB1T3R) | Multi-agent orchestration system |
| [S4F3 3TCH](https://github.com/46b-ETYKiAL/Itasha.Corp_S4F3-3TCH) | ComfyUI custom nodes |
| [S4F3 SH3LL](https://github.com/46b-ETYKiAL/Itasha.Corp_S4F3-SH3LL) | AI coding CLI |

---

## > License

[Apache License 2.0](LICENSE) — Itasha Corp, 2026.

<div align="center">

```
  ┌──────────────────────────────────────────┐
  │                                          │
  │   every signal finds its relay.          │
  │                                          │
  │   ░░░ operator23a is watching ░░░        │
  │                                          │
  └──────────────────────────────────────────┘
```

</div>
