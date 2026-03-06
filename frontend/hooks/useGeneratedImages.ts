'use client';

import { useEffect, useState } from 'react';
import { RoomEvent } from 'livekit-client';
import { useRoomContext } from '@livekit/components-react';

export interface GeneratedImage {
  id: string;
  imageData: string;
  mimeType: string;
  prompt: string;
  timestamp: number;
}

/**
 * Subscribes to "generated-image" data messages published by the agent
 * and returns the list of received images.
 *
 * HACK HERE: extend this hook to persist images, add a gallery, etc.
 */
export function useGeneratedImages(): GeneratedImage[] {
  const room = useRoomContext();
  const [images, setImages] = useState<GeneratedImage[]>([]);

  useEffect(() => {
    const handleData = (
      payload: Uint8Array,
      _participant: unknown,
      _kind: unknown,
      topic?: string
    ) => {
      if (topic !== 'generated-image') return;

      try {
        const data = JSON.parse(new TextDecoder().decode(payload)) as {
          imageData: string;
          mimeType: string;
          prompt: string;
        };

        const image: GeneratedImage = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          imageData: data.imageData,
          mimeType: data.mimeType ?? 'image/png',
          prompt: data.prompt ?? '',
          timestamp: Date.now(),
        };

        setImages((prev) => [...prev, image]);
      } catch (err) {
        console.error('Failed to parse generated-image message:', err);
      }
    };

    room.on(RoomEvent.DataReceived, handleData);
    return () => {
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room]);

  return images;
}
