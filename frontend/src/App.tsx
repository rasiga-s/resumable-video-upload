import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')
const DEFAULT_USER_ID = 'creator_123'
const STORAGE_KEY = 'frameflow.uploads.v1'

type UploadPhase = 'idle' | 'ready' | 'uploading' | 'paused' | 'completed' | 'error'
type PartPhase = 'waiting' | 'uploading' | 'uploaded'

interface UploadSession {
  upload_id: string
  part_size: number
  total_parts: number
}

interface ResumeStatus {
  upload_id: string
  status: string
  file_size: number
  part_size: number
  total_parts: number
  uploaded_parts_count: number
  uploaded_bytes: number
  percentage_complete: number
  missing_part_numbers: number[]
  uploaded_parts: Array<{ part_number: number; etag: string; size_bytes: number }>
}

interface CompleteResponse {
  video_id: string
  object_key: string
  status: string
  message: string
}

interface SavedUpload {
  uploadId: string
  userId: string
  fileName: string
  fileSize: number
  lastModified: number
  savedAt: number
  phase: 'active' | 'completed'
  videoId?: string
  objectKey?: string
}

interface PartView {
  number: number
  phase: PartPhase
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(bytes < 100 * 1024 ** 2 ? 1 : 0)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'Calculating…'
  if (seconds < 60) return `${Math.ceil(seconds)} sec left`
  return `${Math.floor(seconds / 60)} min ${Math.ceil(seconds % 60)} sec left`
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong while uploading.'
}

async function readApiError(response: Response): Promise<string> {
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  if (typeof body?.detail === 'string') return body.detail
  if (Array.isArray(body?.detail)) {
    return body.detail
      .map((issue) => typeof issue === 'object' && issue !== null && 'msg' in issue ? issue.msg : '')
      .filter(Boolean)
      .join(', ')
  }
  return `Request failed with HTTP ${response.status}.`
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init)
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error(`Can't reach the API at ${API_BASE_URL}. Check that Docker Compose is running.`)
    }
    throw error
  }
  if (!response.ok) throw new Error(await readApiError(response))
  return response.json() as Promise<T>
}

function uploadPart(
  url: string,
  chunk: Blob,
  onProgress: (sentBytes: number) => void,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    const abort = () => request.abort()

    request.open('PUT', url)
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded)
    }
    request.onload = () => {
      signal.removeEventListener('abort', abort)
      if (request.status >= 200 && request.status < 300) {
        resolve()
      } else {
        reject(new Error(`Part upload failed with HTTP ${request.status}.`))
      }
    }
    request.onerror = () => {
      signal.removeEventListener('abort', abort)
      reject(new Error('Network error while sending a part to object storage.'))
    }
    request.onabort = () => {
      signal.removeEventListener('abort', abort)
      reject(new DOMException('Upload paused.', 'AbortError'))
    }
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) {
      abort()
      return
    }
    request.send(chunk)
  })
}

function readSavedUploads(): SavedUpload[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(value) ? value as SavedUpload[] : []
  } catch (error) {
    console.warn('Could not read saved upload sessions from browser storage.', error)
    return []
  }
}

function writeSavedUploads(uploads: SavedUpload[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(uploads.slice(0, 8)))
  } catch (error) {
    console.warn('Could not save upload session in browser storage.', error)
  }
}

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const shared = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true as const,
  }

  switch (name) {
    case 'film':
      return <svg {...shared}><rect x="3" y="4" width="18" height="16" rx="3" /><path d="m10 9 5 3-5 3V9Z" /><path d="M7 4v3m5-3v3m5-3v3M7 17v3m5-3v3m5-3v3" /></svg>
    case 'cloud':
      return <svg {...shared}><path d="M7 18a4 4 0 1 1 .8-7.92A5.5 5.5 0 0 1 18.5 12a3 3 0 0 1-.5 6H7Z" /><path d="M12 16v-5m-2 2 2-2 2 2" /></svg>
    case 'upload':
      return <svg {...shared}><path d="M12 16V4m-4 4 4-4 4 4" /><path d="M20 16.5v2A1.5 1.5 0 0 1 18.5 20h-13A1.5 1.5 0 0 1 4 18.5v-2" /></svg>
    case 'check':
      return <svg {...shared}><path d="m5 12 4 4L19 6" /></svg>
    case 'pause':
      return <svg {...shared}><path d="M9 5v14m6-14v14" /></svg>
    case 'play':
      return <svg {...shared}><path d="m8 5 11 7-11 7V5Z" /></svg>
    case 'arrow':
      return <svg {...shared}><path d="M7 17 17 7M7 7h10v10" /></svg>
    case 'close':
      return <svg {...shared}><path d="m18 6-12 12M6 6l12 12" /></svg>
    case 'clock':
      return <svg {...shared}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
    case 'shield':
      return <svg {...shared}><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" /></svg>
    default:
      return <svg {...shared}><circle cx="12" cy="12" r="9" /></svg>
  }
}

function App() {
  const [file, setFile] = useState<File | null>(null)
  const [userId, setUserId] = useState(DEFAULT_USER_ID)
  const [phase, setPhase] = useState<UploadPhase>('idle')
  const [apiOnline, setApiOnline] = useState<boolean | null>(null)
  const [uploadId, setUploadId] = useState('')
  const [partSize, setPartSize] = useState(0)
  const [totalParts, setTotalParts] = useState(0)
  const [uploadedBytes, setUploadedBytes] = useState(0)
  const [livePartBytes, setLivePartBytes] = useState(0)
  const [parts, setParts] = useState<PartView[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [completeData, setCompleteData] = useState<CompleteResponse | null>(null)
  const [savedUploads, setSavedUploads] = useState<SavedUpload[]>(readSavedUploads)
  const [dragging, setDragging] = useState(false)
  const [isFinalizing, setIsFinalizing] = useState(false)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let active = true
    fetch(`${API_BASE_URL}/health`)
      .then((response) => {
        if (!response.ok) throw new Error(`Health endpoint returned HTTP ${response.status}.`)
        return response.json()
      })
      .then(() => { if (active) setApiOnline(true) })
      .catch(() => { if (active) setApiOnline(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    writeSavedUploads(savedUploads)
  }, [savedUploads])

  const matchedUpload = useMemo(() => {
    if (!file) return undefined
    return savedUploads.find((item) =>
      item.phase === 'active' &&
      item.userId === userId.trim() &&
      item.fileName === file.name &&
      item.fileSize === file.size &&
      item.lastModified === file.lastModified,
    )
  }, [file, savedUploads, userId])

  const progressBytes = Math.min(file?.size ?? 0, uploadedBytes + livePartBytes)
  const progressPercent = file && file.size > 0 ? Math.min(100, (progressBytes / file.size) * 100) : 0
  const elapsedSeconds = startedAt ? (Date.now() - startedAt) / 1000 : 0
  const uploadSpeed = elapsedSeconds > 0 ? uploadedBytes / elapsedSeconds : 0
  const remainingSeconds = uploadSpeed > 0 ? (file?.size ?? 0) - progressBytes > 0
    ? ((file!.size - progressBytes) / uploadSpeed)
    : 0 : 0
  const activePart = parts.find((part) => part.phase === 'uploading')?.number
  const completedCount = parts.filter((part) => part.phase === 'uploaded').length

  const acceptFile = (nextFile?: File) => {
    if (!nextFile) return
    if (!nextFile.type.startsWith('video/')) {
      setErrorMessage('Choose a video file to upload.')
      setPhase('error')
      return
    }
    if (nextFile.name.length > 255) {
      setErrorMessage('The filename must be 255 characters or fewer.')
      setPhase('error')
      return
    }
    setFile(nextFile)
    setPhase('ready')
    setErrorMessage('')
    setCompleteData(null)
    setUploadId('')
    setParts([])
    setUploadedBytes(0)
    setLivePartBytes(0)
    setPartSize(0)
    setTotalParts(0)
    setStartedAt(null)
  }

  const saveUpload = useCallback((upload: SavedUpload) => {
    setSavedUploads((current) => [
      upload,
      ...current.filter((item) => item.uploadId !== upload.uploadId),
    ].slice(0, 8))
  }, [])

  const deleteSavedUpload = useCallback(async (item: SavedUpload) => {
    const targetPath = item.videoId
      ? `/uploads/videos/${encodeURIComponent(item.videoId)}?user_id=${encodeURIComponent(item.userId)}`
      : `/uploads/${encodeURIComponent(item.uploadId)}?user_id=${encodeURIComponent(item.userId)}`

    try {
      const result = await apiRequest<{ message: string }>(targetPath, { method: 'DELETE' })
      setSavedUploads((current) => current.filter((entry) => entry.uploadId !== item.uploadId))
      if (file && uploadId === item.uploadId) {
        resetUpload()
      }
      setErrorMessage(result.message || (item.videoId ? 'Video deleted successfully.' : 'Upload session cleared.'))
      setPhase('idle')
    } catch (error) {
      setErrorMessage(getErrorMessage(error))
      setPhase('error')
    }
  }, [file, uploadId])

  const runUpload = async () => {
    if (!file || !userId.trim() || phase === 'uploading') return
    const controller = new AbortController()
    abortRef.current = controller
    setPhase('uploading')
    setErrorMessage('')
    setCompleteData(null)
    setLivePartBytes(0)
    setIsFinalizing(false)
    setStartedAt(Date.now())

    try {
      let sessionId = uploadId
      if (!sessionId && matchedUpload) sessionId = matchedUpload.uploadId
      if (!sessionId) {
        const session = await apiRequest<UploadSession>('/uploads', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            file_size: file.size,
            user_id: userId.trim(),
          }),
          signal: controller.signal,
        })
        sessionId = session.upload_id
        setUploadId(sessionId)
        setPartSize(session.part_size)
        setTotalParts(session.total_parts)
        saveUpload({
          uploadId: sessionId,
          userId: userId.trim(),
          fileName: file.name,
          fileSize: file.size,
          lastModified: file.lastModified,
          savedAt: Date.now(),
          phase: 'active',
        })
      }

      const resume = await apiRequest<ResumeStatus>(
        `/uploads/${encodeURIComponent(sessionId)}/resume?user_id=${encodeURIComponent(userId.trim())}`,
        { signal: controller.signal },
      )
      setUploadId(sessionId)
      setPartSize(resume.part_size)
      setTotalParts(resume.total_parts)
      setUploadedBytes(resume.uploaded_bytes)
      setLivePartBytes(0)
      setParts(Array.from({ length: resume.total_parts }, (_, index) => ({
        number: index + 1,
        phase: resume.missing_part_numbers.includes(index + 1) ? 'waiting' : 'uploaded',
      })))

      for (const partNumber of resume.missing_part_numbers) {
        if (controller.signal.aborted) throw new DOMException('Upload paused.', 'AbortError')
        setParts((current) => current.map((part) => part.number === partNumber ? { ...part, phase: 'uploading' } : part))
        const signed = await apiRequest<{ presigned_url: string }>(
          `/uploads/${encodeURIComponent(sessionId)}/parts/${partNumber}/url?user_id=${encodeURIComponent(userId.trim())}`,
          { signal: controller.signal },
        )
        const offset = (partNumber - 1) * resume.part_size
        const chunk = file.slice(offset, Math.min(offset + resume.part_size, file.size))
        await uploadPart(signed.presigned_url, chunk, setLivePartBytes, controller.signal)
        setUploadedBytes((current) => current + chunk.size)
        setLivePartBytes(0)
        setParts((current) => current.map((part) => part.number === partNumber ? { ...part, phase: 'uploaded' } : part))
      }

      if (controller.signal.aborted) throw new DOMException('Upload paused.', 'AbortError')
      setIsFinalizing(true)
      const completed = await apiRequest<CompleteResponse>(
        `/uploads/${encodeURIComponent(sessionId)}/complete?user_id=${encodeURIComponent(userId.trim())}`,
        { method: 'POST', signal: controller.signal },
      )
      setCompleteData(completed)
      setUploadedBytes(file.size)
      setLivePartBytes(0)
      setPhase('completed')
      saveUpload({
        uploadId: sessionId,
        userId: userId.trim(),
        fileName: file.name,
        fileSize: file.size,
        lastModified: file.lastModified,
        savedAt: Date.now(),
        phase: 'completed',
        videoId: completed.video_id,
        objectKey: completed.object_key,
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setLivePartBytes(0)
        setPhase('paused')
      } else {
        setErrorMessage(getErrorMessage(error))
        setPhase('error')
      }
    } finally {
      setIsFinalizing(false)
      abortRef.current = null
    }
  }

  const pauseUpload = () => {
    abortRef.current?.abort()
  }

  const resetUpload = () => {
    setFile(null)
    setUploadId('')
    setParts([])
    setPhase('idle')
    setErrorMessage('')
    setCompleteData(null)
    setUploadedBytes(0)
    setLivePartBytes(0)
    setPartSize(0)
    setTotalParts(0)
    setStartedAt(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    acceptFile(event.dataTransfer.files[0])
  }

  return (
    <div className="app-shell">
      <main className="main-content">
        <header className="topbar">
          <div className="brand" aria-label="Resumable Video Upload">
            <span className="brand-mark"><Icon name="film" size={21} /></span>
            <span>Resumable<span className="brand-dot">.</span></span>
          </div>
          <div className="topbar-right">
            <span className={`connection-pill ${apiOnline === false ? 'disconnected' : ''}`}>
              <span className="connection-pulse" />{apiOnline === null ? 'Checking API' : apiOnline ? 'API connected' : 'API offline'}
            </span>
          </div>
        </header>

        <section className="page-heading">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" /> RESUMABLE VIDEO UPLOAD</div>
            <h1>Send your video.<br /><span>We’ll handle the rest.</span></h1>
            <p className="heading-copy">Upload directly to storage, watch every part move, and resume if your connection drops.</p>
          </div>
        </section>

        <div className="content-grid">
          <section className="upload-panel panel">
            <div className="panel-heading">
              <div><div className="section-kicker">NEW TRANSFER <span className="kicker-dot" /></div><h2>Upload a video</h2></div>
              <span className="secure-label"><Icon name="shield" size={15} /> PRIVATE &amp; SECURE</span>
            </div>

            {!file ? (
              <div
                className={`dropzone ${dragging ? 'dragging' : ''}`}
                onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
                onDragLeave={() => setDragging(false)}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click() }}
                role="button"
                tabIndex={0}
                aria-label="Choose or drop a video file"
              >
                <input
                  ref={inputRef}
                  className="file-input"
                  type="file"
                  accept="video/*"
                  onChange={(event) => acceptFile(event.target.files?.[0])}
                />
                <div className="drop-graphic">
                  <div className="drop-ring ring-back" /><div className="drop-ring ring-front" />
                  <div className="drop-icon"><Icon name="upload" size={25} /></div>
                  <span className="drop-spark spark-a">✦</span><span className="drop-spark spark-b">·</span>
                </div>
                <h3>Drop your video here</h3>
                <p>or <button className="browse-button" type="button" onClick={(event) => { event.stopPropagation(); inputRef.current?.click() }}>browse files</button> from your device</p>
                <div className="file-specs"><span>MP4, MOV, WEBM</span><i /><span>Any size</span><i /><span>Resumable</span></div>
              </div>
            ) : (
              <div className="selected-file">
                <div className="video-thumb"><Icon name="film" size={27} /><span className="thumb-play"><Icon name="play" size={12} /></span></div>
                <div className="selected-info">
                  <span className="selected-label">{phase === 'completed' ? 'UPLOAD COMPLETE' : matchedUpload || uploadId ? 'READY TO RESUME' : 'READY TO UPLOAD'}</span>
                  <strong title={file.name}>{file.name}</strong>
                  <span>{formatBytes(file.size)} <i /> {file.type || 'Video file'}</span>
                </div>
                {phase !== 'uploading' && phase !== 'completed' && (
                  <button className="remove-file" type="button" aria-label="Remove selected file" onClick={resetUpload}><Icon name="close" size={17} /></button>
                )}
                <input
                  ref={inputRef}
                  className="file-input"
                  type="file"
                  accept="video/*"
                  onChange={(event) => acceptFile(event.target.files?.[0])}
                />
              </div>
            )}

            <div className="user-field">
              <label htmlFor="user-id">UPLOADING AS</label>
              <div className="input-wrap"><span className="input-at">@</span><input id="user-id" value={userId} onChange={(event) => setUserId(event.target.value)} maxLength={100} disabled={phase === 'uploading'} /><span className="input-hint">Your workspace ID</span></div>
            </div>

            {file && (
              <div className="transfer-card" aria-live="polite">
                <div className="transfer-top">
                  <div className={`transfer-status-icon ${phase === 'completed' ? 'done' : phase === 'error' ? 'error' : ''}`}>
                    <Icon name={phase === 'completed' ? 'check' : phase === 'uploading' ? 'cloud' : 'film'} size={17} />
                  </div>
                  <div className="transfer-title">
                    <strong>{phase === 'completed' ? 'Your video is in the library' : phase === 'uploading' && isFinalizing ? 'Assembling your video' : phase === 'uploading' ? `Uploading part ${activePart ?? '…'} of ${totalParts}` : phase === 'paused' ? 'Upload paused safely' : phase === 'error' ? 'Upload needs attention' : 'Transfer ready'}</strong>
                    <span>{phase === 'completed' ? 'All parts verified and assembled' : phase === 'uploading' && isFinalizing ? 'Final checks are happening in object storage' : phase === 'uploading' ? 'Keep this tab open while the transfer runs' : phase === 'paused' ? 'Your confirmed parts are saved in storage' : 'Large files travel in reliable chunks'}</span>
                  </div>
                  <span className={`phase-badge ${phase}`}>{phase === 'uploading' ? 'IN PROGRESS' : phase === 'completed' ? 'READY' : phase === 'paused' ? 'PAUSED' : phase === 'error' ? 'RETRY' : 'STANDBY'}</span>
                </div>
                <div className="progress-caption"><span>OVERALL PROGRESS</span><strong>{progressPercent.toFixed(progressPercent % 1 ? 1 : 0)}%</strong></div>
                <div className="progress-track"><div className={`progress-fill ${phase === 'completed' ? 'complete' : ''}`} style={{ width: `${progressPercent}%` }} /></div>
                <div className="progress-meta"><span>{formatBytes(progressBytes)} <i /> {formatBytes(file.size)}</span><span>{phase === 'uploading' ? formatDuration(remainingSeconds) : `${completedCount || 0} / ${totalParts || '—'} parts`} <i /> {partSize ? `${formatBytes(partSize)} / part` : 'multipart'}</span></div>
                {parts.length > 0 && (
                  <div className="part-grid" aria-label="Upload parts">
                    {parts.map((part) => (
                      <span key={part.number} className={`part-chip ${part.phase}`} title={`Part ${part.number}: ${part.phase}`}>
                        {part.phase === 'uploaded' ? <Icon name="check" size={12} /> : part.number}
                      </span>
                    ))}
                  </div>
                )}
                {errorMessage && <div className="error-banner" role="alert">{errorMessage}</div>}
                {completeData && <div className="success-banner"><Icon name="check" size={16} /><span>Video ID <strong>{completeData.video_id}</strong> · {completeData.status}</span></div>}
              </div>
            )}

            <div className="upload-actions">
              {phase === 'uploading' && isFinalizing ? (
                <button className="primary-button" type="button" disabled><Icon name="clock" size={17} /> Finalizing video…</button>
              ) : phase === 'uploading' ? (
                <button className="primary-button pause-button" type="button" onClick={pauseUpload}><Icon name="pause" size={17} /> Pause upload</button>
              ) : phase === 'completed' ? (
                <button className="primary-button" type="button" onClick={resetUpload}>Upload another <Icon name="arrow" size={17} /></button>
              ) : (
                <button className="primary-button" type="button" disabled={!file || !userId.trim() || apiOnline === false} onClick={runUpload}>
                  <Icon name={phase === 'paused' || phase === 'error' || matchedUpload ? 'play' : 'upload'} size={17} />
                  {phase === 'paused' || phase === 'error' || matchedUpload ? 'Resume upload' : 'Start upload'}
                  <span className="button-arrow">↗</span>
                </button>
              )}
              <span className="action-note"><Icon name="shield" size={14} /> Your original file stays on your device</span>
            </div>
          </section>

        </div>

        <section className="recent-section">
          <div className="recent-heading"><div><div className="section-kicker">YOUR SPACE <span className="kicker-dot" /></div><h2>Recent transfers</h2></div><span className="recent-count">{savedUploads.length} SAVED LOCALLY</span></div>
          {savedUploads.length === 0 ? (
            <div className="empty-recent"><span className="empty-icon"><Icon name="film" size={18} /></span><span>Your completed and resumable transfers will show up here.</span></div>
          ) : (
            <div className="recent-list">
              {savedUploads.slice(0, 4).map((item) => (
                <div className="recent-item" key={item.uploadId}>
                  <div className={`recent-file-icon ${item.phase}`}><Icon name={item.phase === 'completed' ? 'check' : 'film'} size={17} /></div>
                  <div className="recent-file-copy"><strong title={item.fileName}>{item.fileName}</strong><span>{formatBytes(item.fileSize)} <i /> {new Date(item.savedAt).toLocaleDateString()}</span></div>
                  <span className={`recent-status ${item.phase}`}>{item.phase === 'completed' ? 'READY' : 'RESUMABLE'}</span>
                  {item.phase === 'active' && <button className="resume-link" type="button" onClick={() => { setUploadId(item.uploadId); setUserId(item.userId); setPhase('ready'); inputRef.current?.click() }}>Resume <Icon name="arrow" size={13} /></button>}
                  {item.phase === 'completed' && item.videoId && (
                    <button className="delete-link" type="button" onClick={() => { void deleteSavedUpload(item) }}>Delete</button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <footer className="page-footer"><span>RESUMABLE VIDEO UPLOAD</span></footer>
      </main>
    </div>
  )
}

export default App
