import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, finalize } from 'rxjs';
import { ArtifexApiService } from './artifex-api.service';
import {
  CapabilitiesResponse,
  ChatRequest,
  HealthResponse,
  HistoryMessage,
  ResponseFormat
} from './api.models';

interface DisplayMessage extends HistoryMessage {
  readonly imageName?: string;
  readonly requestId?: string;
}

interface SelectedImage {
  readonly file: File;
  readonly previewUrl: string;
  readonly base64: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss'
})
export class AppComponent implements OnInit, OnDestroy {
  public prompt = '';
  public systemPrompt = 'You are a precise visual-analysis assistant. Follow the user request exactly.';
  public apiKey = '';
  public maxNewTokens = 1024;
  public responseFormat: ResponseFormat = 'text';
  public messages: DisplayMessage[] = [];
  public selectedImage: SelectedImage | null = null;
  public busy = false;
  public error = '';
  public health: HealthResponse | null = null;
  public capabilities: CapabilitiesResponse | null = null;

  private readonly subscriptions = new Subscription();

  public constructor(private readonly api: ArtifexApiService) {}

  public ngOnInit(): void {
    this.subscriptions.add(
      this.api.health().subscribe({
        next: (health) => (this.health = health),
        error: () => (this.health = null)
      })
    );
    this.subscriptions.add(
      this.api.capabilities().subscribe({
        next: (capabilities) => {
          this.capabilities = capabilities;
          this.maxNewTokens = Math.min(this.maxNewTokens, capabilities.maxRequestNewTokens);
        },
        error: () => (this.capabilities = null)
      })
    );
  }

  public ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
    this.revokeImagePreview();
  }

  public async selectImage(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.item(0);
    if (!file) {
      return;
    }
    this.error = '';
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      this.error = 'Select a JPEG, PNG, or WebP image.';
      input.value = '';
      return;
    }
    if (this.capabilities && file.size > this.capabilities.maxUploadBytes) {
      this.error = `The selected image exceeds the server upload limit of ${this.formatBytes(this.capabilities.maxUploadBytes)}.`;
      input.value = '';
      return;
    }
    this.revokeImagePreview();
    const dataUrl = await this.readAsDataUrl(file);
    const comma = dataUrl.indexOf(',');
    this.selectedImage = {
      file,
      previewUrl: URL.createObjectURL(file),
      base64: comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl
    };
  }

  public removeImage(input?: HTMLInputElement): void {
    this.revokeImagePreview();
    this.selectedImage = null;
    if (input) {
      input.value = '';
    }
  }

  public clearConversation(): void {
    if (this.busy) {
      return;
    }
    this.messages = [];
    this.error = '';
  }

  public submit(): void {
    const prompt = this.prompt.trim();
    if (!prompt || this.busy) {
      return;
    }
    this.error = '';
    const history: HistoryMessage[] = this.messages.map(({ role, content }) => ({ role, content }));
    const image = this.selectedImage;
    const request: ChatRequest = {
      prompt,
      systemPrompt: this.systemPrompt.trim(),
      history,
      maxNewTokens: this.maxNewTokens,
      responseFormat: this.responseFormat,
      ...(image
        ? { imageBase64: image.base64, imageMimeType: image.file.type }
        : {})
    };
    this.messages = [
      ...this.messages,
      { role: 'user', content: prompt, imageName: image?.file.name }
    ];
    this.prompt = '';
    this.removeImage();
    this.busy = true;

    this.subscriptions.add(
      this.api
        .chat(request, this.apiKey)
        .pipe(finalize(() => (this.busy = false)))
        .subscribe({
          next: (response) => {
            this.messages = [
              ...this.messages,
              {
                role: 'assistant',
                content: response.output,
                requestId: response.requestId
              }
            ];
          },
          error: (error: HttpErrorResponse) => {
            const detail = error.error?.detail;
            this.error = typeof detail === 'string'
              ? detail
              : `Request failed with HTTP ${error.status || 'error'}.`;
          }
        })
    );
  }

  public handleComposerKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.submit();
    }
  }

  public formatBytes(value: number): string {
    if (value < 1024) {
      return `${value} B`;
    }
    if (value < 1024 * 1024) {
      return `${(value / 1024).toFixed(1)} KB`;
    }
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  private revokeImagePreview(): void {
    if (this.selectedImage?.previewUrl) {
      URL.revokeObjectURL(this.selectedImage.previewUrl);
    }
  }

  private readAsDataUrl(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result ?? ''));
      reader.onerror = () => reject(reader.error ?? new Error('Unable to read image.'));
      reader.readAsDataURL(file);
    });
  }
}
