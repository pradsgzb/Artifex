import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import {
  CapabilitiesResponse,
  ChatRequest,
  GenerationResponse,
  HealthResponse
} from './api.models';

@Injectable({ providedIn: 'root' })
export class ArtifexApiService {
  private readonly baseUrl = '/api/v1';

  public constructor(private readonly http: HttpClient) {}

  public health(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>(`${this.baseUrl}/health`);
  }

  public capabilities(): Observable<CapabilitiesResponse> {
    return this.http.get<CapabilitiesResponse>(`${this.baseUrl}/capabilities`);
  }

  public chat(request: ChatRequest, apiKey: string): Observable<GenerationResponse> {
    const headers = apiKey.trim()
      ? new HttpHeaders({ 'X-API-Key': apiKey.trim() })
      : undefined;
    return this.http.post<GenerationResponse>(`${this.baseUrl}/chat`, request, { headers });
  }
}
