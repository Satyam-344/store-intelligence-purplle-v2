import { useEffect, useRef, useState } from 'react'

interface UseWebSocketOptions {
  onOpen?: () => void
  onClose?: () => void
  reconnectInterval?: number
}

export function useWebSocket(url: string, options: UseWebSocketOptions = {}) {
  const [lastMessage, setLastMessage] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { onOpen, onClose, reconnectInterval = 3000 } = options

  useEffect(() => {
    const connect = () => {
      try {
        const ws = new WebSocket(url)
        wsRef.current = ws

        ws.onopen = () => {
          onOpen?.()
          if (reconnectTimeout.current) {
            clearTimeout(reconnectTimeout.current)
            reconnectTimeout.current = null
          }
        }

        ws.onmessage = (event) => {
          setLastMessage(event.data)
        }

        ws.onclose = () => {
          onClose?.()
          reconnectTimeout.current = setTimeout(connect, reconnectInterval)
        }

        ws.onerror = () => {
          ws.close()
        }
      } catch (e) {
        reconnectTimeout.current = setTimeout(connect, reconnectInterval)
      }
    }

    connect()

    return () => {
      wsRef.current?.close()
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current)
    }
  }, [url])

  return { lastMessage }
}
