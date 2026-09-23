export interface AppConfig {
    appId: string;
    appSecret: string;
    pbxBase: string;

    geminiApiKey: string;
    darazApiBaseUrl: string;
    geminiVoice?: string; // Fallback when agent profile has no voice
    geminiModel?: string;
    geminiSilenceDurationMs?: number;

    agentProfile?: string;
    agentInstructions?: string;
    companyName?: string;
    agentName?: string;
    initialGreeting: string;

    speakOnRouteFailure: boolean;
    routeFailureUserReply: string;

    /**
     * Optional extra MCP servers (in addition to 3CX `{pbxBase}/mcp`).
     * See `@3cx-examples/mcp`. Omit or leave empty to use only 3CX MCP.
     */
    customMcpServers?: import('@3cx-examples/mcp').CustomMcpServerConfig[];
}

import { existsSync, readFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { load } from 'js-yaml';

function loadConfig(): AppConfig {
    const projectEnvPath = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../../.env');
    if (existsSync(projectEnvPath)) {
        if (typeof process.loadEnvFile !== 'function') {
            throw new Error('Node.js 20.12+ is required to load the project .env file.');
        }
        process.loadEnvFile(projectEnvPath);
    }

    const configPath = resolve(process.cwd(), 'config.yaml');
    const raw = load(readFileSync(configPath, 'utf-8')) as Partial<AppConfig>;
    return {
        ...raw,
        appId: process.env.THREECX_APP_ID ?? raw.appId ?? '',
        appSecret: process.env.THREECX_APP_SECRET ?? raw.appSecret ?? '',
        pbxBase: process.env.THREECX_PBX_BASE_URL ?? raw.pbxBase ?? '',
        geminiApiKey: process.env.GOOGLE_API_KEY ?? raw.geminiApiKey ?? '',
        darazApiBaseUrl: process.env.DARAZ_API_BASE_URL
            ?? raw.darazApiBaseUrl
            ?? 'http://127.0.0.1:8000/api/v1',
    } as AppConfig;
}

export default loadConfig();
