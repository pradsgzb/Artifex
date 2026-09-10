export type ResponseFormat = 'text' | 'json';
export type ChatRole = 'user' | 'assistant';

export interface HistoryMessage {
  readonly role: ChatRole;
  readonly content: string;
}

export interface ChatRequest {
  readonly prompt: string;
  readonly systemPrompt: string;
  readonly history: readonly HistoryMessage[];
  readonly maxNewTokens: number;
  readonly responseFormat: ResponseFormat;
  readonly imageBase64?: string;
  readonly imageMimeType?: string;
}

export interface GenerationResponse {
  readonly requestId: string;
  readonly model: string;
  readonly output: string;
  readonly responseFormat: ResponseFormat;
  readonly jsonValue: unknown | null;
  readonly createdAt: string;
}

export interface HealthResponse {
  readonly status: string;
  readonly model: string;
  readonly modelLoaded: boolean;
  readonly activeRequests: number;
  readonly queuedRequests: number;
}

export interface CapabilitiesResponse {
  readonly maxUploadBytes: number;
  readonly maxPromptChars: number;
  readonly maxHistoryMessages: number;
  readonly maxRequestNewTokens: number;
  readonly apiKeyConfigured: boolean;
}
