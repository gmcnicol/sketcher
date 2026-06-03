import type { FormEvent } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
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

type CandidateSummary = {
  id: string
  runId: string
  generationId: string
  generationNumber: number
  position: number
  originType: string
  genome: Record<string, unknown>
  artifactPath: string | null
  byteSize: number | null
  sha256: string | null
  validationStatus: string
  validationMessage: string | null
}

type GenerationSummary = {
  id: string
  runId: string
  generationNumber: number
  status: string
  totalCandidateCount: number
  readyCount: number
  failedCount: number
  candidates: CandidateSummary[]
}

type GenerationResponse = {
  generation: GenerationSummary
}

type ReviewState = {
  runId: string
  generationId: string
  generationNumber: number
  currentCandidate: CandidateSummary | null
  currentIndex: number
  totalReadyCount: number
  survivorCount: number
  rejectedCount: number
  reviewedCount: number
  complete: boolean
}

type ReviewResponse = {
  review: ReviewState
}

type ReviewDecision = 'survived' | 'rejected'

const apiBaseUrl =
  import.meta.env.VITE_MODEL_BUILDER_URL ?? 'http://localhost:8000'

function App() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [result, setResult] = useState<UploadResponse | null>(null)
  const [generation, setGeneration] = useState<GenerationSummary | null>(null)
  const [review, setReview] = useState<ReviewState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [generationError, setGenerationError] = useState<string | null>(null)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isReviewing, setIsReviewing] = useState(false)

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
      setGeneration(null)
      setReview(null)
      return
    }

    const formData = new FormData()
    formData.append('file', selectedFile)

    setIsUploading(true)
    setError(null)
    setResult(null)
    setGeneration(null)
    setReview(null)
    setGenerationError(null)
    setReviewError(null)

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

  async function handleGenerateCandidates() {
    if (!result) {
      return
    }

    setIsGenerating(true)
    setGenerationError(null)

    try {
      const response = await fetch(
        `${apiBaseUrl}/runs/${result.run.id}/generations`,
        {
          method: 'POST',
        },
      )
      const body = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(body?.detail ?? 'Candidate generation failed.')
      }

      const generationBody = body as GenerationResponse
      setGeneration(generationBody.generation)
      await fetchReview(result.run.id)
    } catch (generateError) {
      setGenerationError(
        generateError instanceof Error
          ? generateError.message
          : 'Candidate generation failed unexpectedly.',
      )
    } finally {
      setIsGenerating(false)
    }
  }

  const fetchReview = useCallback(async (runId: string) => {
    const response = await fetch(`${apiBaseUrl}/runs/${runId}/review/current`)
    const body = await response.json().catch(() => null)

    if (!response.ok) {
      throw new Error(body?.detail ?? 'Review state failed to load.')
    }

    setReview((body as ReviewResponse).review)
  }, [])

  const submitReviewDecision = useCallback(
    async (decision: ReviewDecision) => {
      if (isReviewing || !result || !review?.currentCandidate || review.complete) {
        return
      }

      setIsReviewing(true)
      setReviewError(null)

      try {
        const response = await fetch(
          `${apiBaseUrl}/runs/${result.run.id}/review/decisions`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              candidateId: review.currentCandidate.id,
              decision,
            }),
          },
        )
        const body = await response.json().catch(() => null)

        if (!response.ok) {
          throw new Error(body?.detail ?? 'Review decision failed.')
        }

        setReview((body as ReviewResponse).review)
      } catch (decisionError) {
        setReviewError(
          decisionError instanceof Error
            ? decisionError.message
            : 'Review decision failed unexpectedly.',
        )
      } finally {
        setIsReviewing(false)
      }
    },
    [isReviewing, result, review],
  )

  const undoReviewDecision = useCallback(async () => {
    if (isReviewing || !result || !review || review.reviewedCount === 0) {
      return
    }

    setIsReviewing(true)
    setReviewError(null)

    try {
      const response = await fetch(
        `${apiBaseUrl}/runs/${result.run.id}/review/undo`,
        { method: 'POST' },
      )
      const body = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(body?.detail ?? 'Undo failed.')
      }

      setReview((body as ReviewResponse).review)
    } catch (undoError) {
      setReviewError(
        undoError instanceof Error ? undoError.message : 'Undo failed unexpectedly.',
      )
    } finally {
      setIsReviewing(false)
    }
  }, [isReviewing, result, review])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (isEditableTarget(event.target)) {
        return
      }

      const key = event.key.toLowerCase()
      if (key === 'j') {
        event.preventDefault()
        void submitReviewDecision('survived')
      } else if (key === 'k') {
        event.preventDefault()
        void submitReviewDecision('rejected')
      } else if (key === 'u') {
        event.preventDefault()
        void undoReviewDecision()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [submitReviewDecision, undoReviewDecision])

  const currentCandidate = review?.currentCandidate ?? null

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
                setGeneration(null)
                setReview(null)
                setGenerationError(null)
                setReviewError(null)
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
          <>
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

            <button
              className="generation-action"
              type="button"
              onClick={handleGenerateCandidates}
              disabled={isGenerating || generation !== null}
            >
              {isGenerating ? 'Generating...' : 'Generate candidates'}
            </button>
          </>
        ) : null}

        {generationError ? (
          <div className="status status-error" role="alert">
            <span>Generation error</span>
            <p>{generationError}</p>
          </div>
        ) : null}

        {generation ? (
          <ReviewDeck
            generation={generation}
            review={review}
            candidate={currentCandidate}
            isReviewing={isReviewing}
            reviewError={reviewError}
            onSurvive={() => void submitReviewDecision('survived')}
            onReject={() => void submitReviewDecision('rejected')}
            onUndo={() => void undoReviewDecision()}
          />
        ) : null}
      </section>
    </main>
  )
}

type ReviewDeckProps = {
  generation: GenerationSummary
  review: ReviewState | null
  candidate: CandidateSummary | null
  isReviewing: boolean
  reviewError: string | null
  onSurvive: () => void
  onReject: () => void
  onUndo: () => void
}

function ReviewDeck({
  generation,
  review,
  candidate,
  isReviewing,
  reviewError,
  onSurvive,
  onReject,
  onUndo,
}: ReviewDeckProps) {
  if (!review) {
    return (
      <section className="review-deck" aria-label="Review candidates">
        <div className="review-empty">Loading review deck...</div>
      </section>
    )
  }

  if (review.complete) {
    return (
      <section className="review-deck" aria-label="Review complete">
        <div className="review-complete">
          <p className="eyebrow">Generation {review.generationNumber}</p>
          <h2>Review complete</h2>
          <div className="review-counts" aria-label="Review totals">
            <span>{review.reviewedCount} reviewed</span>
            <span>{review.survivorCount} survived</span>
            <span>{review.rejectedCount} rejected</span>
          </div>
          <button
            className="secondary-action"
            type="button"
            onClick={onUndo}
            disabled={isReviewing || review.reviewedCount === 0}
          >
            Undo (u)
          </button>
          {reviewError ? (
            <div className="status status-error" role="alert">
              <span>Review error</span>
              <p>{reviewError}</p>
            </div>
          ) : null}
        </div>
      </section>
    )
  }

  if (!candidate) {
    return (
      <section className="review-deck" aria-label="Review candidates">
        <div className="review-empty">
          No ready candidates in generation {generation.generationNumber}.
        </div>
      </section>
    )
  }

  return (
    <section className="review-deck" aria-label="Review candidates">
      <div className="review-header">
        <div>
          <p className="eyebrow">Generation {review.generationNumber}</p>
          <h2>
            Candidate {review.currentIndex} of {review.totalReadyCount}
          </h2>
        </div>
        <div className="review-counts" aria-label="Review counts">
          <span>{review.survivorCount} survived</span>
          <span>{review.rejectedCount} rejected</span>
        </div>
      </div>

      <div className="candidate-stage">
        <img
          key={candidate.id}
          src={`${apiBaseUrl}/candidates/${encodeURIComponent(candidate.id)}/artifact`}
          alt={`Candidate ${review.currentIndex} artifact`}
        />
      </div>

      <div className="candidate-meta" aria-label="Candidate metadata">
        <div>
          <span>Origin</span>
          <code>{formatOrigin(candidate.originType)}</code>
        </div>
        <div>
          <span>Position</span>
          <code>{candidate.position}</code>
        </div>
        <div>
          <span>Survivors</span>
          <code>{review.survivorCount}</code>
        </div>
      </div>

      <div className="review-actions" aria-label="Review actions">
        <button type="button" onClick={onSurvive} disabled={isReviewing}>
          Survive (j)
        </button>
        <button
          className="reject-action"
          type="button"
          onClick={onReject}
          disabled={isReviewing}
        >
          Reject (k)
        </button>
        <button
          className="secondary-action"
          type="button"
          onClick={onUndo}
          disabled={isReviewing || review.reviewedCount === 0}
        >
          Undo (u)
        </button>
      </div>

      {reviewError ? (
        <div className="status status-error" role="alert">
          <span>Review error</span>
          <p>{reviewError}</p>
        </div>
      ) : null}

      <details className="debug-panel">
        <summary>Debug</summary>
        <div>
          <span>Candidate ID</span>
          <code>{candidate.id}</code>
        </div>
        <div>
          <span>Generation ID</span>
          <code>{candidate.generationId}</code>
        </div>
        <div>
          <span>Artifact path</span>
          <code>{candidate.artifactPath ?? 'none'}</code>
        </div>
        <div>
          <span>SHA-256</span>
          <code>{candidate.sha256 ?? 'none'}</code>
        </div>
        <div>
          <span>Validation</span>
          <code>
            {candidate.validationStatus}
            {candidate.validationMessage ? `: ${candidate.validationMessage}` : ''}
          </code>
        </div>
        <div>
          <span>Origin type</span>
          <code>{candidate.originType}</code>
        </div>
        <div>
          <span>Genome JSON</span>
          <pre>{JSON.stringify(candidate.genome, null, 2)}</pre>
        </div>
      </details>
    </section>
  )
}

function formatByteSize(byteSize: number) {
  if (byteSize < 1024) {
    return `${byteSize} B`
  }

  return `${(byteSize / 1024).toFixed(1)} KB`
}

function formatOrigin(originType: string) {
  return originType.replaceAll('_', ' ')
}

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  return (
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT' ||
    target.isContentEditable
  )
}

export default App
