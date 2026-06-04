import type { FormEvent } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
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
  parentCandidateIds: string[]
  parentGenerationId: string | null
  createdAt: string
}

type GenerationSummary = {
  id: string
  runId: string
  generationNumber: number
  status: string
  totalCandidateCount: number
  readyCount: number
  failedCount: number
  reviewedCount: number
  survivorCount: number
  rejectedCount: number
  lowDiversity: boolean
  canBreedNextGeneration: boolean
  canRerollGeneration: boolean
  createdAt: string
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
type NextGenerationMode = 'breed' | 'reroll'
type TerminalGenerationStatus = 'ready' | 'partial_failed'

const apiBaseUrl =
  import.meta.env.VITE_MODEL_BUILDER_URL ?? '/api'
const generationReadyTarget = 24
const generationPollIntervalMs = 1000

function App() {
  const prefersReducedMotion = useReducedMotion()
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
  const [generationRequestInFlight, setGenerationRequestInFlight] =
    useState(false)
  const [generationStartedAt, setGenerationStartedAt] = useState<number | null>(
    null,
  )
  const [lastProgressAt, setLastProgressAt] = useState<number | null>(null)
  const [generationActionLabel, setGenerationActionLabel] = useState(
    'Generating candidates',
  )
  const [loadedCandidateImageId, setLoadedCandidateImageId] = useState<string | null>(
    null,
  )
  const generationActionInFlightRef = useRef(false)
  const generationRequestBaselineRef = useRef(0)
  const reviewedTerminalGenerationRef = useRef<string | null>(null)
  const reviewActionInFlightRef = useRef(false)

  const selectedFileLabel = useMemo(() => {
    if (!selectedFile) {
      return 'No SVG selected'
    }

    return `${selectedFile.name} (${formatByteSize(selectedFile.size)})`
  }, [selectedFile])
  const currentCandidate = review?.currentCandidate ?? null
  const candidateImageLoaded =
    currentCandidate !== null && loadedCandidateImageId === currentCandidate.id
  const generationIsReviewable =
    generation !== null &&
    isTerminalGenerationStatus(generation.status) &&
    generation.readyCount > 0 &&
    !generationRequestInFlight

  const fetchReview = useCallback(async (runId: string) => {
    const response = await fetch(`${apiBaseUrl}/runs/${runId}/review/current`)
    const body = await response.json().catch(() => null)

    if (!response.ok) {
      throw new Error(body?.detail ?? 'Review state failed to load.')
    }

    setReview((body as ReviewResponse).review)
  }, [])

  const startGenerationProgress = useCallback(
    (actionLabel: string) => {
      generationRequestBaselineRef.current = generation?.generationNumber ?? 0
      reviewedTerminalGenerationRef.current = null
      setGenerationActionLabel(actionLabel)
      setGenerationStartedAt(Date.now())
      setLastProgressAt(null)
      setGenerationRequestInFlight(true)
    },
    [generation],
  )

  const handleGenerationUpdate = useCallback(
    async (nextGeneration: GenerationSummary) => {
      if (
        generationRequestInFlight &&
        nextGeneration.generationNumber <= generationRequestBaselineRef.current
      ) {
        return
      }

      if (nextGeneration.id !== generation?.id) {
        setReview(null)
        setLoadedCandidateImageId(null)
      }

      setGeneration(nextGeneration)
      setLastProgressAt(Date.now())

      if (!isTerminalGenerationStatus(nextGeneration.status)) {
        return
      }

      setGenerationRequestInFlight(false)

      if (
        nextGeneration.readyCount > 0 &&
        reviewedTerminalGenerationRef.current !== nextGeneration.id
      ) {
        reviewedTerminalGenerationRef.current = nextGeneration.id
        await fetchReview(result?.run.id ?? nextGeneration.runId)
      }
    },
    [fetchReview, generation?.id, generationRequestInFlight, result],
  )

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedFile) {
      setError('Choose an SVG file before starting a run.')
      setResult(null)
      setGeneration(null)
      setReview(null)
      setGenerationRequestInFlight(false)
      setGenerationStartedAt(null)
      setLastProgressAt(null)
      return
    }

    const formData = new FormData()
    formData.append('file', selectedFile)

    setIsUploading(true)
    setError(null)
    setResult(null)
    setGeneration(null)
    setReview(null)
    setGenerationRequestInFlight(false)
    setGenerationStartedAt(null)
    setLastProgressAt(null)
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
    if (generationActionInFlightRef.current || !result) {
      return
    }

    generationActionInFlightRef.current = true
    setIsGenerating(true)
    setGenerationError(null)
    startGenerationProgress('Generating candidates')

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
      await handleGenerationUpdate(generationBody.generation)
    } catch (generateError) {
      setGenerationRequestInFlight(false)
      setGenerationError(
        generateError instanceof Error
          ? generateError.message
          : 'Candidate generation failed unexpectedly.',
      )
    } finally {
      setIsGenerating(false)
      generationActionInFlightRef.current = false
    }
  }

  const submitReviewDecision = useCallback(
    async (decision: ReviewDecision) => {
      if (
        reviewActionInFlightRef.current ||
        isReviewing ||
        !candidateImageLoaded ||
        !result ||
        !review?.currentCandidate ||
        review.complete
      ) {
        return
      }

      reviewActionInFlightRef.current = true
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
        reviewActionInFlightRef.current = false
      }
    },
    [candidateImageLoaded, isReviewing, result, review],
  )

  const undoReviewDecision = useCallback(async () => {
    if (
      reviewActionInFlightRef.current ||
      isReviewing ||
      !result ||
      !review ||
      review.reviewedCount === 0
    ) {
      return
    }

    reviewActionInFlightRef.current = true
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
      reviewActionInFlightRef.current = false
    }
  }, [isReviewing, result, review])

  const createNextGeneration = useCallback(
    async (mode: NextGenerationMode) => {
      if (
        generationActionInFlightRef.current ||
        isGenerating ||
        !result ||
        !review?.complete
      ) {
        return
      }

      generationActionInFlightRef.current = true
      setIsGenerating(true)
      setReviewError(null)
      setGenerationError(null)
      startGenerationProgress(
        mode === 'breed' ? 'Breeding next generation' : 'Rerolling generation',
      )

      try {
        const response = await fetch(
          `${apiBaseUrl}/runs/${result.run.id}/generations/next`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
          },
        )
        const body = await response.json().catch(() => null)

        if (!response.ok) {
          throw new Error(body?.detail ?? 'Next generation failed.')
        }

        const generationBody = body as GenerationResponse
        await handleGenerationUpdate(generationBody.generation)
      } catch (nextGenerationError) {
        setGenerationRequestInFlight(false)
        setReviewError(
          nextGenerationError instanceof Error
            ? nextGenerationError.message
            : 'Next generation failed unexpectedly.',
        )
      } finally {
        setIsGenerating(false)
        generationActionInFlightRef.current = false
      }
    },
    [
      handleGenerationUpdate,
      isGenerating,
      result,
      review,
      startGenerationProgress,
    ],
  )

  useEffect(() => {
    const runId = result?.run.id
    const shouldPoll =
      runId !== undefined &&
      (generationRequestInFlight || generation?.status === 'running')

    if (!shouldPoll) {
      return
    }

    let cancelled = false
    let requestInProgress = false

    async function pollCurrentGeneration() {
      if (!runId || requestInProgress) {
        return
      }

      requestInProgress = true
      try {
        const response = await fetch(
          `${apiBaseUrl}/runs/${runId}/generations/current`,
        )
        const body = await response.json().catch(() => null)

        if (cancelled) {
          return
        }

        if (response.status === 404 && generationRequestInFlight) {
          return
        }

        if (!response.ok) {
          throw new Error(body?.detail ?? 'Generation progress failed to load.')
        }

        await handleGenerationUpdate((body as GenerationResponse).generation)
      } catch (progressError) {
        if (cancelled) {
          return
        }

        setGenerationError(
          progressError instanceof Error
            ? progressError.message
            : 'Generation progress failed unexpectedly.',
        )
      } finally {
        requestInProgress = false
      }
    }

    void pollCurrentGeneration()
    const interval = window.setInterval(
      () => void pollCurrentGeneration(),
      generationPollIntervalMs,
    )

    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [
    generation?.status,
    generationRequestInFlight,
    handleGenerationUpdate,
    result?.run.id,
  ])

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
      } else if (
        key === 'b' &&
        review?.complete &&
        review.survivorCount > 0
      ) {
        event.preventDefault()
        void createNextGeneration('breed')
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [createNextGeneration, review, submitReviewDecision, undoReviewDecision])

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
                setGenerationRequestInFlight(false)
                setGenerationStartedAt(null)
                setLastProgressAt(null)
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
            <section className="run-strip" aria-label="Upload result">
              <div className="run-strip-item">
                <span>Source</span>
                <strong>{result.source.filename}</strong>
              </div>
              <div className="run-strip-item">
                <span>Run</span>
                <code>{shortId(result.run.id)}</code>
              </div>
            </section>

            <RunDebugDisclosure result={result} />

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

        <AnimatePresence>
          {generationRequestInFlight || generation ? (
            <GenerationProgress
              key="generation-progress"
              actionLabel={generationActionLabel}
              generation={generation}
              isInFlight={generationRequestInFlight}
              startedAt={generationStartedAt}
              lastProgressAt={lastProgressAt}
            />
          ) : null}
        </AnimatePresence>

        <AnimatePresence mode="wait">
          {generationIsReviewable && generation ? (
            <motion.div
              key={generation.id}
              initial={
                prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 10 }
              }
              animate={
                prefersReducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }
              }
              exit={
                prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 6 }
              }
              transition={{ duration: 0.18 }}
            >
              <ReviewDeck
                generation={generation}
                review={review}
                candidate={currentCandidate}
                isReviewing={isReviewing}
                isGenerating={isGenerating}
                candidateImageLoaded={candidateImageLoaded}
                reviewError={reviewError}
                onSurvive={() => void submitReviewDecision('survived')}
                onReject={() => void submitReviewDecision('rejected')}
                onUndo={() => void undoReviewDecision()}
                onNextGeneration={(mode) => void createNextGeneration(mode)}
                onCandidateImageLoad={(candidateId) =>
                  setLoadedCandidateImageId(candidateId)
                }
                onCandidateImageError={() => setLoadedCandidateImageId(null)}
              />
            </motion.div>
          ) : null}
        </AnimatePresence>
      </section>
    </main>
  )
}

type ReviewDeckProps = {
  generation: GenerationSummary
  review: ReviewState | null
  candidate: CandidateSummary | null
  isReviewing: boolean
  isGenerating: boolean
  candidateImageLoaded: boolean
  reviewError: string | null
  onSurvive: () => void
  onReject: () => void
  onUndo: () => void
  onNextGeneration: (mode: NextGenerationMode) => void
  onCandidateImageLoad: (candidateId: string) => void
  onCandidateImageError: () => void
}

type GenerationProgressProps = {
  actionLabel: string
  generation: GenerationSummary | null
  isInFlight: boolean
  startedAt: number | null
  lastProgressAt: number | null
}

function GenerationProgress({
  actionLabel,
  generation,
  isInFlight,
  startedAt,
  lastProgressAt,
}: GenerationProgressProps) {
  const prefersReducedMotion = useReducedMotion()
  const [now, setNow] = useState(() => Date.now())
  const activeGeneration =
    generation && (!isInFlight || generation.status === 'running')
      ? generation
      : null
  const status = activeGeneration?.status ?? 'starting'
  const readyCount = activeGeneration?.readyCount ?? 0
  const failedCount = activeGeneration?.failedCount ?? 0
  const totalCandidateCount = activeGeneration?.totalCandidateCount ?? 0
  const readyPercent = Math.min(
    (readyCount / generationReadyTarget) * 100,
    100,
  )
  const isStarting = activeGeneration === null
  const isWarning = status === 'partial_failed'

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [])

  return (
    <motion.section
      className={`generation-progress${
        isWarning ? ' generation-progress-warning' : ''
      }`}
      aria-label="Generation progress"
      initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={prefersReducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
      exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 6 }}
      transition={{ duration: 0.18 }}
    >
      <div className="generation-progress-header">
        <div>
          <p className="eyebrow">{actionLabel}</p>
          <h2>
            {activeGeneration
              ? `Generation ${activeGeneration.generationNumber}`
              : 'Starting generation'}
          </h2>
        </div>
        <span className="generation-progress-status">
          {formatStatus(status)}
        </span>
      </div>

      <div
        className={`generation-progress-track${
          isStarting ? ' generation-progress-track-indeterminate' : ''
        }`}
        aria-label={`${readyCount} ready candidates out of ${generationReadyTarget}`}
      >
        <motion.div
          className="generation-progress-fill"
          style={{ width: isStarting ? '44%' : `${readyPercent}%` }}
          transition={{ duration: prefersReducedMotion ? 0 : 0.2 }}
        />
      </div>

      <div className="generation-progress-grid">
        <AnimatedMetric
          label="Ready"
          value={`${readyCount} / ${generationReadyTarget}`}
        />
        <AnimatedMetric label="Failed" value={failedCount.toString()} />
        <AnimatedMetric
          label="Attempts"
          value={totalCandidateCount.toString()}
        />
        <AnimatedMetric
          label="Elapsed"
          value={startedAt ? formatDuration(now - startedAt) : '0s'}
        />
        <AnimatedMetric
          label="Updated"
          value={lastProgressAt ? `${formatDuration(now - lastProgressAt)} ago` : 'waiting'}
        />
      </div>
    </motion.section>
  )
}

type AnimatedMetricProps = {
  label: string
  value: string
}

function AnimatedMetric({ label, value }: AnimatedMetricProps) {
  const prefersReducedMotion = useReducedMotion()

  return (
    <div className="generation-progress-metric">
      <span>{label}</span>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.strong
          key={value}
          initial={
            prefersReducedMotion
              ? { opacity: 0 }
              : { opacity: 0, y: -3, scale: 0.98 }
          }
          animate={
            prefersReducedMotion
              ? { opacity: 1 }
              : { opacity: 1, y: 0, scale: 1 }
          }
          exit={
            prefersReducedMotion
              ? { opacity: 0 }
              : { opacity: 0, y: 3, scale: 0.98 }
          }
          transition={{ duration: 0.16 }}
        >
          {value}
        </motion.strong>
      </AnimatePresence>
    </div>
  )
}

function RunDebugDisclosure({ result }: { result: UploadResponse }) {
  const [open, setOpen] = useState(false)
  const prefersReducedMotion = useReducedMotion()

  return (
    <section className="debug-panel run-debug" aria-label="Run debug metadata">
      <button
        className="debug-summary"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        Debug metadata
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.section
            className="debug-panel-content"
            initial={
              prefersReducedMotion ? { opacity: 0 } : { opacity: 0, height: 0 }
            }
            animate={
              prefersReducedMotion ? { opacity: 1 } : { opacity: 1, height: 'auto' }
            }
            exit={
              prefersReducedMotion ? { opacity: 0 } : { opacity: 0, height: 0 }
            }
            transition={{ duration: 0.18 }}
          >
            <div>
              <span>Source filename</span>
              <code>{result.source.filename}</code>
            </div>
            <div>
              <span>Source ID</span>
              <code>{result.source.id}</code>
            </div>
            <div>
              <span>Run ID</span>
              <code>{result.run.id}</code>
            </div>
            <div>
              <span>Run status</span>
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
            <div>
              <span>Byte size</span>
              <code>{formatByteSize(result.source.byteSize)}</code>
            </div>
            <div>
              <span>Run source ID</span>
              <code>{result.run.sourceId}</code>
            </div>
          </motion.section>
        ) : null}
      </AnimatePresence>
    </section>
  )
}

function ReviewDeck({
  generation,
  review,
  candidate,
  isReviewing,
  isGenerating,
  candidateImageLoaded,
  reviewError,
  onSurvive,
  onReject,
  onUndo,
  onNextGeneration,
  onCandidateImageLoad,
  onCandidateImageError,
}: ReviewDeckProps) {
  if (!review) {
    return (
      <section className="review-deck" aria-label="Review candidates">
        <div className="review-empty">Loading review deck...</div>
      </section>
    )
  }

  if (review.complete) {
    const hasSurvivors = review.survivorCount > 0
    const hasLowDiversity = review.survivorCount > 0 && review.survivorCount <= 2
    const isBusy = isReviewing || isGenerating

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
          {hasLowDiversity ? (
            <p className="review-note">
              Low survivor diversity. The next generation will include extra fresh
              candidates.
            </p>
          ) : null}
          {!hasSurvivors ? (
            <p className="review-note">
              No survivors remain. Reroll the next generation with fresh candidates.
            </p>
          ) : null}
          <div className="review-complete-actions">
            {hasSurvivors ? (
              <button
                type="button"
                onClick={() => onNextGeneration('breed')}
                disabled={isBusy}
              >
                {isGenerating ? 'Breeding...' : 'Breed next generation (b)'}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => onNextGeneration('reroll')}
                disabled={isBusy}
              >
                {isGenerating ? 'Rerolling...' : 'Reroll generation'}
              </button>
            )}
            <button
              className="secondary-action"
              type="button"
              onClick={onUndo}
              disabled={isBusy || review.reviewedCount === 0}
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

  const reviewActionsDisabled = isReviewing || !candidateImageLoaded

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
          onLoad={() => onCandidateImageLoad(candidate.id)}
          onError={onCandidateImageError}
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
        <button type="button" onClick={onSurvive} disabled={reviewActionsDisabled}>
          Survive (j)
        </button>
        <button
          className="reject-action"
          type="button"
          onClick={onReject}
          disabled={reviewActionsDisabled}
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

function formatDuration(durationMs: number) {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000))

  if (totalSeconds < 60) {
    return `${totalSeconds}s`
  }

  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds.toString().padStart(2, '0')}s`
}

function formatStatus(status: string) {
  return status.replaceAll('_', ' ')
}

function formatOrigin(originType: string) {
  return originType.replaceAll('_', ' ')
}

function shortId(id: string) {
  return id.slice(0, 8)
}

function isTerminalGenerationStatus(
  status: string,
): status is TerminalGenerationStatus {
  return status === 'ready' || status === 'partial_failed'
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
