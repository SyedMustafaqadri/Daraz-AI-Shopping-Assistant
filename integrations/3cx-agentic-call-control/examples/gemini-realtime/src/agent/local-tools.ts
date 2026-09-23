import type { AgentProfile } from './agent-profiles.ts';

export interface LocalToolDef {
    type: 'function';
    function: {
        name: string;
        description: string;
        parameters: Record<string, unknown>;
    };
}

export const TOOL_UPDATE_SCREENING: LocalToolDef = {
    type: 'function',
    function: {
        name: 'update_screening',
        description: 'Save caller name, company, or reason for calling.',
        parameters: {
            type: 'object',
            properties: {
                field: { type: 'string', enum: ['name', 'company', 'reason'], description: 'Which field to set' },
                value: { type: 'string', description: 'The value for the field' },
            },
            required: ['field', 'value'],
        },
    },
};

export const TOOL_TRANSFER_CALL: LocalToolDef = {
    type: 'function',
    function: {
        name: 'transfer_call',
        description: 'Transfer the current call to an extension.',
        parameters: {
            type: 'object',
            properties: {
                destination: { type: 'string', description: 'Extension number to transfer to' },
            },
            required: ['destination'],
        },
    },
};

export const TOOL_DROP_CALL: LocalToolDef = {
    type: 'function',
    function: {
        name: 'drop_call',
        description: 'Hang up the current call.',
        parameters: { type: 'object', properties: {} },
    },
};

export const TOOL_TRANSFER_TO_VOICEMAIL: LocalToolDef = {
    type: 'function',
    function: {
        name: 'transfer_to_voicemail',
        description: 'Send the current call to voicemail of the specified extension.',
        parameters: {
            type: 'object',
            properties: {
                destination: { type: 'string', description: 'Extension number whose voicemail to send to' },
            },
            required: ['destination'],
        },
    },
};

export const TOOL_SEARCH_PRODUCTS: LocalToolDef = {
    type: 'function',
    function: {
        name: 'search_products',
        description: 'Search Daraz.pk for products matching the caller’s request and optional budget.',
        parameters: {
            type: 'object',
            properties: {
                query: { type: 'string', description: 'Product or category to search for.' },
                min_price: { type: 'number', description: 'Minimum price in PKR, if specified.' },
                max_price: { type: 'number', description: 'Maximum price in PKR, if specified.' },
            },
            required: ['query'],
        },
    },
};

export const TOOL_GET_PRODUCT: LocalToolDef = {
    type: 'function',
    function: {
        name: 'get_product',
        description: 'Get validated details for a Daraz product using its ID.',
        parameters: {
            type: 'object',
            properties: {
                product_id: { type: 'string', description: 'Daraz product ID, such as i927677133.' },
            },
            required: ['product_id'],
        },
    },
};

export function buildLocalTools(opts?: {
    callScreening?: boolean;
    allowedActions?: string[];
}): LocalToolDef[] {
    const allowedActions = opts?.allowedActions;
    const tools: LocalToolDef[] = [
        TOOL_SEARCH_PRODUCTS,
        TOOL_GET_PRODUCT,
    ];
    if (!allowedActions || allowedActions.includes('transfer')) {
        tools.push(TOOL_TRANSFER_CALL, TOOL_TRANSFER_TO_VOICEMAIL);
    }
    if (!allowedActions || allowedActions.includes('drop')) {
        tools.push(TOOL_DROP_CALL);
    }
    if (opts?.callScreening) {
        tools.push(TOOL_UPDATE_SCREENING);
    }
    return tools;
}

export function isExtensionAllowed(
    ext: string,
    profile: AgentProfile,
): boolean {
    if (profile.allowedExtensions?.length) {
        return profile.allowedExtensions.some((rule) => matchExtensionRule(ext, rule));
    }
    if (profile.blockedExtensions?.length) {
        return !profile.blockedExtensions.some((rule) => matchExtensionRule(ext, rule));
    }
    return true;
}

function matchExtensionRule(ext: string, rule: string): boolean {
    if (rule.includes('-')) {
        const [lo, hi] = rule.split('-');
        const n = parseInt(ext, 10);
        return n >= parseInt(lo, 10) && n <= parseInt(hi, 10);
    }
    return ext === rule;
}
