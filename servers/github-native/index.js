#!/usr/bin/env node
/** github-native MCP server - wraps gh CLI for GitHub access without Docker */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

async function checkPrerequisites() {
  try { await execFileAsync("gh", ["--version"]); }
  catch { console.error("gh not found"); process.exit(1); }
  try { await execFileAsync("gh", ["auth", "status"]); }
  catch { console.error("not authenticated"); process.exit(1); }
}

/** Run gh with args array (execFile for security). */
async function runGh(args) {
  const { stdout } = await execFileAsync("gh", args, { maxBuffer: 10 * 1024 * 1024 });
  return JSON.parse(stdout);
}

async function runGhRaw(args) {
  const { stdout } = await execFileAsync("gh", args, { maxBuffer: 10 * 1024 * 1024 });
  return stdout;
}

const TOOLS = [
  {
    name: "create_issue",
    description: "Create a new GitHub issue in a repository.",
    inputSchema: {
      type: "object",
      properties: {
        owner: { type: "string", description: "Repository owner (user or org)" },
        repo: { type: "string", description: "Repository name" },
        title: { type: "string", description: "Issue title" },
        body: { type: "string", description: "Issue body (markdown)" },
        labels: { type: "array", items: { type: "string" }, description: "Labels to add" },
        assignees: { type: "array", items: { type: "string" }, description: "GitHub usernames to assign" },
      },
      required: ["owner", "repo", "title"],
    },
  },
  {
    name: "list_issues",
    description: "List issues in a GitHub repository.",
    inputSchema: {
      type: "object",
      properties: {
        owner: { type: "string", description: "Repository owner" },
        repo: { type: "string", description: "Repository name" },
        state: { type: "string", enum: ["open", "closed", "all"], description: "Filter by state (default: open)" },
        limit: { type: "number", description: "Max issues to return (default: 30)" },
      },
      required: ["owner", "repo"],
    },
  },
  {
    name: "get_issue",
    description: "Get details of a specific GitHub issue.",
    inputSchema: {
      type: "object",
      properties: {
        owner: { type: "string", description: "Repository owner" },
        repo: { type: "string", description: "Repository name" },
        number: { type: "number", description: "Issue number" },
      },
      required: ["owner", "repo", "number"],
    },
  },
  {
    name: "create_pull_request",
    description: "Create a new pull request in a GitHub repository.",
    inputSchema: {
      type: "object",
      properties: {
        owner: { type: "string", description: "Repository owner" },
        repo: { type: "string", description: "Repository name" },
        title: { type: "string", description: "PR title" },
        body: { type: "string", description: "PR body (markdown)" },
        head: { type: "string", description: "Head branch (source)" },
        base: { type: "string", description: "Base branch (target)" },
      },
      required: ["owner", "repo", "title", "head", "base"],
    },
  },
  {
    name: "list_pull_requests",
    description: "List pull requests in a GitHub repository.",
    inputSchema: {
      type: "object",
      properties: {
        owner: { type: "string", description: "Repository owner" },
        repo: { type: "string", description: "Repository name" },
        state: { type: "string", enum: ["open", "closed", "merged", "all"], description: "Filter by state (default: open)" },
        limit: { type: "number", description: "Max PRs to return (default: 30)" },
      },
      required: ["owner", "repo"],
    },
  },
  {
    name: "get_file_contents",
    description: "Get the contents of a file from a GitHub repository.",
    inputSchema: {
      type: "object",
      properties: {
        owner: { type: "string", description: "Repository owner" },
        repo: { type: "string", description: "Repository name" },
        path: { type: "string", description: "File path within the repository" },
        ref: { type: "string", description: "Git ref (branch, tag, SHA). Default: default branch." },
      },
      required: ["owner", "repo", "path"],
    },
  },
  {
    name: "search_repositories",
    description: "Search for GitHub repositories.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        limit: { type: "number", description: "Max results (default: 10)" },
      },
      required: ["query"],
    },
  },
  {
    name: "search_code",
    description: "Search for code across GitHub repositories.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        limit: { type: "number", description: "Max results (default: 10)" },
      },
      required: ["query"],
    },
  },
];

async function handleTool(name, args) {
  const repo = [args.owner, args.repo].join(String.fromCharCode(47));
  switch (name) {
    case "create_issue": {
      const g=["issue","create","--repo",repo,"--title",args.title,"--json","number,url"];
      if (args.body) g.push("--body", args.body);
      if (args.labels && args.labels.length > 0) g.push("--label", args.labels.join(","));
      if (args.assignees && args.assignees.length > 0) g.push("--assignee", args.assignees.join(","));
      return JSON.stringify(await runGh(g), null, 2);
    }
    case "list_issues": {
      const g=["issue","list","--repo",repo,"--json","number,title,state,url","--limit",String(args.limit||30)];
      if (args.state) g.push("--state", args.state);
      return JSON.stringify(await runGh(g), null, 2);
    }
    case "get_issue": {
      const g=["issue","view",String(args.number),"--repo",repo,"--json","number,title,body,state,labels,assignees,url"];
      return JSON.stringify(await runGh(g), null, 2);
    }
    case "create_pull_request": {
      const g=["pr","create","--repo",repo,"--title",args.title,"--head",args.head,"--base",args.base,"--json","number,url"];
      if (args.body) g.push("--body", args.body);
      return JSON.stringify(await runGh(g), null, 2);
    }
    case "list_pull_requests": {
      const g=["pr","list","--repo",repo,"--json","number,title,state,url","--limit",String(args.limit||30)];
      if (args.state) g.push("--state", args.state);
      return JSON.stringify(await runGh(g), null, 2);
    }
    case "get_file_contents": {
      const apiPath = ["repos", args.owner, args.repo, "contents", args.path].join(String.fromCharCode(47));
      const g = ["api", apiPath];
      if (args.ref) g.push("-f", "ref=" + args.ref);
      const raw = await runGhRaw(g);
      const data = JSON.parse(raw);
      if (data.type === "file" && data.content) {
        const decoded = Buffer.from(data.content, "base64").toString("utf-8");
        return JSON.stringify({ name: data.name, path: data.path, sha: data.sha, size: data.size, content: decoded }, null, 2);
      }
      return JSON.stringify(data, null, 2);
    }
    case "search_repositories": {
      const g=["search","repos",args.query,"--json","fullName,description,url","--limit",String(args.limit||10)];
      return JSON.stringify(await runGh(g), null, 2);
    }
    case "search_code": {
      const g=["search","code",args.query,"--json","repository,path,textMatches","--limit",String(args.limit||10)];
      return JSON.stringify(await runGh(g), null, 2);
    }
    default:
      throw new Error("Unknown tool: " + name);
  }
}

async function main() {
  await checkPrerequisites();

  const server = new Server(
    { name: "github-native", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: TOOLS,
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    try {
      const result = await handleTool(name, args);
      return { content: [{ type: "text", text: result }] };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return { content: [{ type: "text", text: "Error: " + message }], isError: true };
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((e) => { console.error(e.message); process.exit(1); });
