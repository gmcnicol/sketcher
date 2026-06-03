import type { FormEvent } from 'react'
import { useMemo, useState } from 'react'
import './App.css'

type UploadSource = {
  id: string
  filename: string
  sha256: string
  byteSize: number
  artifactPath: string
}

type UploadRun = {
  id: string
  sourceId: string
  status: string
}

type UploadResponse = {
  source: UploadSource
  run: UploadRun
}

const apiBaseUrl =
  import.meta.env.VITE_MODEL_BUILDER_URL ?? 'http://localhost:8000'

function App() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [result, setResult] = useState<UploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)

  const selectedFileLabel = useMemo(() => {
    if (!selectedFile) {
      return 'No SVG selected'
    }

    return `${selectedFile.name} (${formatByteSize(selectedFile.size)})`
  }, [selectedFile])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedFile) {
      setError('Choose an SVG file before starting a run.')
      setResult(null)
      return
    }

    const formData = new FormData()
    formData.append('file', selectedFile)

    setIsUploading(true)
    setError(null)
    setResult(null)

    try {
      const response = await fetch(`${apiBaseUrl}/sources`, {
        method: 'POST',
        body: formData,
      })
      const body = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(body?.detail ?? 'Upload failed.')
      }

      setResult(body as UploadResponse)
    } catch (uploadError) {
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : 'Upload failed unexpectedly.',
      )
    } finally {
      setIsUploading(false)
    }
  }

  return (
    <main className="app-shell">
      <section className="upload-panel" aria-labelledby="upload-title">
        <div className="upload-heading">
          <p className="eyebrow">Sketcher</p>
          <h1 id="upload-title">Create an evolution run</h1>
        </div>

        <form className="upload-form" onSubmit={handleSubmit}>
          <label className="file-drop">
            <span className="file-drop-label">Source SVG</span>
            <span className="file-drop-name">{selectedFileLabel}</span>
            <input
              type="file"
              accept=".svg,image/svg+xml"
              onChange={(event) => {
                setSelectedFile(event.target.files?.[0] ?? null)
                setError(null)
                setResult(null)
              }}
            />
          </label>

          <button type="submit" disabled={isUploading}>
            {isUploading ? 'Uploading...' : 'Start run'}
          </button>
        </form>

        {error ? (
          <div className="status status-error" role="alert">
            <span>Error</span>
            <p>{error}</p>
          </div>
        ) : null}

        {result ? (
          <section className="status status-success" aria-label="Upload result">
            <div>
              <span>Source ID</span>
              <code>{result.source.id}</code>
            </div>
            <div>
              <span>Run ID</span>
              <code>{result.run.id}</code>
            </div>
            <div>
              <span>Status</span>
              <code>{result.run.status}</code>
            </div>
            <div>
              <span>SHA-256</span>
              <code>{result.source.sha256}</code>
            </div>
            <div>
              <span>Stored path</span>
              <code>{result.source.artifactPath}</code>
            </div>
          </section>
        ) : null}
      </section>
    </main>
  )
}

function formatByteSize(byteSize: number) {
  if (byteSize < 1024) {
    return `${byteSize} B`
  }

  return `${(byteSize / 1024).toFixed(1)} KB`
}

export default App
