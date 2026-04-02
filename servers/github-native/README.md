---
title: GitHub Native MCP Server
created: 2026-03-07
---

# GitHub Native MCP Server

Replaces Docker-based github-mcp-server with a native gh CLI wrapper.

## Prerequisites

- Node.js >= 18
- gh CLI installed: cli.github.com
- gh authenticated: gh auth login

## Tools

- create_issue
- list_issues
- get_issue
- create_pull_request
- list_pull_requests
- get_file_contents
- search_repositories
- search_code

## Security

All gh calls use execFile with array args. No shell injection possible.
