import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('motion/react', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  const ignoredMotionProps = new Set([
    'animate',
    'exit',
    'initial',
    'layout',
    'transition',
    'whileHover',
    'whileTap',
  ])
  const motion = new Proxy(
    {},
    {
      get: (_target, tag: string) =>
        function MotionElement({
          children,
          ...props
        }: {
          children?: React.ReactNode
          [key: string]: unknown
        }) {
          const elementProps = Object.fromEntries(
            Object.entries(props).filter(([key]) => !ignoredMotionProps.has(key)),
          )
          return React.createElement(tag, elementProps, children)
        },
    },
  )

  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      React.createElement(React.Fragment, null, children),
    motion,
    useReducedMotion: () => true,
  }
})

import App from '../src/App'

type Candidate = {
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
  reviewDecision: 'survived' | 'rejected' | null
  parentCandidateIds: string[]
  parentGenerationId: string | null
  createdAt: string
}

type Generation = {
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
  candidates: Candidate[]
}

type Review = {
  runId: string
  generationId: string
  generationNumber: number
  currentCandidate: Candidate | null
  currentIndex: number
  totalReadyCount: number
  survivorCount: number
  rejectedCount: number
  reviewedCount: number
  complete: boolean
}

type HistorySource = {
  id: string
  filename: string
  sha256: string
  byteSize: number
  artifactPath: string
  createdAt: string
}

type RunHistoryEntry = {
  id: string
  sourceId: string
  status: string
  createdAt: string
  source: HistorySource
  generations: Generation[]
}

type SurvivorVideoExport = {
  runId: string
  status: 'not_started' | 'queued' | 'running' | 'complete' | 'failed'
  survivorCount: number
  holdMilliseconds: number
  transitionMilliseconds: number
  fps: number
  fullVideo: {
    path: string
    url: string
    byteSize: number
    sha256: string
  } | null
  shorts: Array<{
    index: number
    startSeconds: number
    endSeconds: number
    path: string
    url: string
    byteSize: number
    sha256: string
  }>
  error: string | null
  createdAt: string | null
  updatedAt: string | null
}

const source = {
  id: '019b0000-0000-7000-8000-000000000001',
  filename: 'source.svg',
  sha256: 'a'.repeat(64),
  byteSize: 128,
  artifactPath: 'artifacts/sources/source.svg',
}
const run = {
  id: '019b0000-0000-7000-8000-000000000002',
  sourceId: source.id,
  status: 'active',
}
const uploadResponse = { source, run }

beforeEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('App MVP flow', () => {
  it('uploads an SVG, shows the run strip, and toggles debug metadata', async () => {
    const user = userEvent.setup()
    const fetchMock = installFetch()
    render(<App />)

    await uploadSourceSvg(user)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sources',
      expect.objectContaining({
        method: 'POST',
        body: expect.any(FormData),
      }),
    )
    expect(await screen.findByText('source.svg')).toBeTruthy()
    expect(screen.getByText(run.id.slice(0, 8))).toBeTruthy()

    const debugButton = screen.getByRole('button', { name: /debug metadata/i })
    expect(debugButton).toHaveAttribute('aria-expanded', 'false')

    await user.click(debugButton)

    expect(debugButton).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Source ID')).toBeTruthy()
    expect(screen.getAllByText(source.id)).toHaveLength(2)
    expect(screen.getByText(source.sha256)).toBeTruthy()
    expect(screen.getByText('128 B')).toBeTruthy()
  })

  it('loads previous runs and opens survivor history', async () => {
    const user = userEvent.setup()
    const survivor = candidate({
      id: 'history-survivor',
      position: 1,
      reviewDecision: 'survived',
    })
    const rejected = candidate({
      id: 'history-rejected',
      position: 2,
      reviewDecision: 'rejected',
    })
    const historyGeneration = generation({
      candidates: [survivor, rejected],
      reviewedCount: 2,
      survivorCount: 1,
      rejectedCount: 1,
    })
    const olderGeneration = generation({
      id: 'older-generation',
      candidates: [
        candidate({
          id: 'older-candidate',
          generationId: 'older-generation',
          position: 7,
        }),
      ],
    })
    installFetch({
      historyRuns: [
        runHistory({
          generations: [historyGeneration],
        }),
        runHistory({
          id: 'older-run',
          sourceId: 'older-source',
          source: {
            ...source,
            id: 'older-source',
            filename: 'older-source.svg',
            createdAt: '2026-06-03T00:00:00+00:00',
          },
          generations: [olderGeneration],
        }),
      ],
      review: review({
        currentCandidate: null,
        reviewedCount: 2,
        survivorCount: 1,
        rejectedCount: 1,
        complete: false,
      }),
    })
    render(<App />)

    expect(screen.queryByLabelText('Run history')).toBeNull()

    await user.click(screen.getByRole('button', { name: /previous runs/i }))

    expect(await screen.findByLabelText('Run history')).toBeTruthy()
    expect(screen.getAllByText('1 survived').length).toBeGreaterThan(0)
    expect(screen.getAllByText('1 rejected').length).toBeGreaterThan(0)
    expect(screen.getByText('survived')).toBeTruthy()
    expect(screen.getByText('rejected')).toBeTruthy()
    expect(
      screen.queryByRole('img', { name: /generation 1 candidate 7/i }),
    ).toBeNull()

    await user.click(screen.getByRole('button', { name: /view details/i }))
    expect(
      await screen.findByRole('img', { name: /generation 1 candidate 7/i }),
    ).toBeTruthy()

    await user.click(screen.getAllByRole('button', { name: /open run/i })[0])

    expect(await screen.findByLabelText('Generation progress')).toBeTruthy()
    expect(screen.queryByLabelText('Run history')).toBeNull()
    expect(screen.getAllByText('Generation 1')).toBeTruthy()
  })

  it('generates candidates, shows progress, loads review state, and renders PNG image URLs', async () => {
    const user = userEvent.setup()
    const firstCandidate = candidate({ id: 'candidate-1' })
    const failedCandidate = candidate({
      id: 'candidate-failed',
      position: 2,
      validationStatus: 'failed',
      validationMessage: 'Rendered candidate artifact is too large.',
    })
    const firstGeneration = generation({
      candidates: [firstCandidate, failedCandidate],
      status: 'partial_failed',
      failedCount: 1,
      totalCandidateCount: 25,
    })
    const fetchMock = installFetch({
      generation: firstGeneration,
      review: review({
        generationId: firstGeneration.id,
        currentCandidate: firstCandidate,
      }),
    })
    render(<App />)

    await uploadSourceSvg(user)
    await user.click(screen.getByRole('button', { name: /generate candidates/i }))

    expect(await screen.findByLabelText('Generation progress')).toBeTruthy()
    expect(screen.getAllByText('Generation 1')).toHaveLength(3)
    expect(screen.getByText('24 / 24')).toBeTruthy()
    expect(screen.getByLabelText('Candidate previews')).toBeTruthy()
    expect(screen.getByLabelText('Candidate 1 current')).toBeTruthy()
    expect(screen.getByText('Partial failed')).toBeTruthy()
    expect(screen.getByText('Candidate issues')).toBeTruthy()
    expect(screen.getByText(/Rendered candidate artifact is too large/i)).toBeTruthy()
    expect(await screen.findByText('Candidate 1 of 24')).toBeTruthy()

    expect(screen.getByRole('img', { name: /candidate 1 thumbnail/i })).toHaveAttribute(
      'src',
      '/api/candidates/candidate-1/thumbnail.png?size=256',
    )

    const image = screen.getByRole('img', { name: /^candidate 1 preview$/i })
    expect(image).toHaveAttribute(
      'src',
      '/api/candidates/candidate-1/thumbnail.png?size=1024',
    )
    await waitFor(() => expect(prewarmCallCount(fetchMock)).toBe(1))
  })

  it('submits j/k/u keyboard review actions only after image load and ignores editable targets', async () => {
    const user = userEvent.setup()
    const candidates = [
      candidate({ id: 'candidate-1', position: 1 }),
      candidate({ id: 'candidate-2', position: 2 }),
      candidate({ id: 'candidate-3', position: 3 }),
    ]
    const fetchMock = installFetch({
      generation: generation({ candidates }),
      review: review({ currentCandidate: candidates[0] }),
      decisionReviews: [
        review({
          currentCandidate: candidates[1],
          currentIndex: 2,
          reviewedCount: 1,
          survivorCount: 1,
        }),
        review({
          currentCandidate: candidates[2],
          currentIndex: 3,
          reviewedCount: 2,
          survivorCount: 1,
          rejectedCount: 1,
        }),
      ],
      undoReviews: [
        review({
          currentCandidate: candidates[1],
          currentIndex: 2,
          reviewedCount: 1,
          survivorCount: 1,
        }),
      ],
    })
    render(<App />)

    await uploadSourceSvg(user)
    await user.click(screen.getByRole('button', { name: /generate candidates/i }))
    const firstImage = await screen.findByRole('img', {
      name: /^candidate 1 preview$/i,
    })

    await user.keyboard('j')
    expect(decisionBodies(fetchMock)).toEqual([])
    expect(screen.getByRole('img', { name: /candidate 2 thumbnail/i })).toHaveAttribute(
      'src',
      '/api/candidates/candidate-2/thumbnail.png?size=256',
    )

    const textarea = document.createElement('textarea')
    document.body.append(textarea)
    textarea.focus()
    fireEvent.load(firstImage)
    await user.keyboard('j')
    expect(decisionBodies(fetchMock)).toEqual([])
    textarea.remove()

    await user.keyboard('j')
    await screen.findByRole('img', { name: /^candidate 2 preview$/i })
    expect(await screen.findByLabelText('Candidate 1 survived')).toBeTruthy()
    expect(screen.getByRole('img', { name: /candidate 1 thumbnail/i })).toHaveAttribute(
      'src',
      '/api/candidates/candidate-1/thumbnail.png?size=256',
    )
    expect(screen.getByLabelText('Candidate 2 current')).toBeTruthy()
    expect(decisionBodies(fetchMock)).toEqual([
      { candidateId: 'candidate-1', decision: 'survived' },
    ])

    fireEvent.load(screen.getByRole('img', { name: /^candidate 2 preview$/i }))
    await user.keyboard('k')
    await screen.findByRole('img', { name: /^candidate 3 preview$/i })
    expect(await screen.findByLabelText('Candidate 2 rejected')).toBeTruthy()
    expect(screen.getByLabelText('Candidate 3 current')).toBeTruthy()
    expect(decisionBodies(fetchMock)).toEqual([
      { candidateId: 'candidate-1', decision: 'survived' },
      { candidateId: 'candidate-2', decision: 'rejected' },
    ])

    await user.keyboard('u')
    await screen.findByRole('img', { name: /^candidate 2 preview$/i })
    expect(await screen.findByLabelText('Candidate 2 current')).toBeTruthy()
    expect(undoCallCount(fetchMock)).toBe(1)
  })

  it('routes low-diversity completed reviews to breed the next generation', async () => {
    const user = userEvent.setup()
    const fetchMock = installFetch({
      generation: generation({
        reviewedCount: 24,
        survivorCount: 2,
        rejectedCount: 22,
        lowDiversity: true,
        canBreedNextGeneration: true,
      }),
      review: review({
        currentCandidate: null,
        reviewedCount: 24,
        survivorCount: 2,
        rejectedCount: 22,
        complete: true,
      }),
      nextGeneration: generation({
        id: 'generation-2',
        generationNumber: 2,
      }),
      nextReview: review({
        generationId: 'generation-2',
        generationNumber: 2,
        currentCandidate: candidate({
          id: 'candidate-next',
          generationId: 'generation-2',
          generationNumber: 2,
        }),
      }),
    })
    render(<App />)

    await uploadSourceSvg(user)
    await user.click(screen.getByRole('button', { name: /generate candidates/i }))
    expect(await screen.findByText(/low survivor diversity/i)).toBeTruthy()

    await user.click(
      screen.getByRole('button', { name: /breed next generation/i }),
    )

    await waitFor(() => expect(nextGenerationBodies(fetchMock)).toEqual([{ mode: 'breed' }]))
  })

  it('routes zero-survivor completed reviews to reroll the generation', async () => {
    const user = userEvent.setup()
    const fetchMock = installFetch({
      generation: generation({
        reviewedCount: 24,
        survivorCount: 0,
        rejectedCount: 24,
        canRerollGeneration: true,
      }),
      review: review({
        currentCandidate: null,
        reviewedCount: 24,
        survivorCount: 0,
        rejectedCount: 24,
        complete: true,
      }),
      nextGeneration: generation({
        id: 'generation-2',
        generationNumber: 2,
      }),
      nextReview: review({
        generationId: 'generation-2',
        generationNumber: 2,
        currentCandidate: candidate({
          id: 'candidate-next',
          generationId: 'generation-2',
          generationNumber: 2,
        }),
      }),
    })
    render(<App />)

    await uploadSourceSvg(user)
    await user.click(screen.getByRole('button', { name: /generate candidates/i }))
    expect(await screen.findByText(/no survivors remain/i)).toBeTruthy()

    await user.click(screen.getByRole('button', { name: /reroll generation/i }))

    await waitFor(() => expect(nextGenerationBodies(fetchMock)).toEqual([{ mode: 'reroll' }]))
  })

  it('starts survivor video export and shows completed downloads', async () => {
    const user = userEvent.setup()
    const completedExport = survivorVideoExport({
      status: 'complete',
      survivorCount: 2,
      fullVideo: {
        path: 'artifacts/exports/survivor-videos/run/full.mp4',
        url: `/runs/${run.id}/exports/survivor-video/full.mp4`,
        byteSize: 2048,
        sha256: 'c'.repeat(64),
      },
      shorts: [
        {
          index: 1,
          startSeconds: 0,
          endSeconds: 60,
          path: 'artifacts/exports/survivor-videos/run/short-1.mp4',
          url: `/runs/${run.id}/exports/survivor-video/shorts/1.mp4`,
          byteSize: 1024,
          sha256: 'd'.repeat(64),
        },
      ],
    })
    const fetchMock = installFetch({
      startedSurvivorExport: completedExport,
    })
    render(<App />)

    await uploadSourceSvg(user)
    await user.click(screen.getByRole('button', { name: /export survivor videos/i }))

    expect(exportStartCallCount(fetchMock)).toBe(1)
    expect(await screen.findByRole('link', { name: /download 4k video/i })).toHaveAttribute(
      'href',
      `/api/runs/${run.id}/exports/survivor-video/full.mp4`,
    )
    expect(screen.getByRole('link', { name: /short 01/i })).toHaveAttribute(
      'href',
      `/api/runs/${run.id}/exports/survivor-video/shorts/1.mp4`,
    )
  })
})

async function uploadSourceSvg(user: ReturnType<typeof userEvent.setup>) {
  const file = new File(
    ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'],
    'source.svg',
    { type: 'image/svg+xml' },
  )

  await user.upload(screen.getByLabelText(/source svg/i), file)
  await user.click(screen.getByRole('button', { name: /start run/i }))
}

function candidate(overrides: Partial<Candidate> = {}): Candidate {
  return {
    id: 'candidate-1',
    runId: run.id,
    generationId: 'generation-1',
    generationNumber: 1,
    position: 1,
    originType: 'preset_mutation',
    genome: {
      schemaVersion: 1,
      strategyFamily: 'outline_retrace',
      renderParameters: { seed: 1 },
    },
    artifactPath: 'artifacts/candidates/candidate-1.svg',
    byteSize: 64,
    sha256: 'b'.repeat(64),
    validationStatus: 'ready',
    validationMessage: 'Candidate SVG passed validation.',
    reviewDecision: null,
    parentCandidateIds: [],
    parentGenerationId: null,
    createdAt: '2026-06-04T00:00:00+00:00',
    ...overrides,
  }
}

function generation(overrides: Partial<Generation> = {}): Generation {
  const candidates = overrides.candidates ?? [candidate()]

  return {
    id: 'generation-1',
    runId: run.id,
    generationNumber: 1,
    status: 'ready',
    totalCandidateCount: 24,
    readyCount: 24,
    failedCount: 0,
    reviewedCount: 0,
    survivorCount: 0,
    rejectedCount: 0,
    lowDiversity: false,
    canBreedNextGeneration: false,
    canRerollGeneration: false,
    createdAt: '2026-06-04T00:00:00+00:00',
    candidates,
    ...overrides,
  }
}

function review(overrides: Partial<Review> = {}): Review {
  const activeCandidate = overrides.currentCandidate ?? candidate()

  return {
    runId: run.id,
    generationId: activeCandidate.generationId,
    generationNumber: activeCandidate.generationNumber,
    currentCandidate: activeCandidate,
    currentIndex: 1,
    totalReadyCount: 24,
    survivorCount: 0,
    rejectedCount: 0,
    reviewedCount: 0,
    complete: false,
    ...overrides,
  }
}

function runHistory(overrides: Partial<RunHistoryEntry> = {}): RunHistoryEntry {
  return {
    id: run.id,
    sourceId: source.id,
    status: 'active',
    createdAt: '2026-06-04T00:00:00+00:00',
    source: {
      ...source,
      createdAt: '2026-06-04T00:00:00+00:00',
    },
    generations: [],
    ...overrides,
  }
}

function survivorVideoExport(
  overrides: Partial<SurvivorVideoExport> = {},
): SurvivorVideoExport {
  return {
    runId: run.id,
    status: 'not_started',
    survivorCount: 0,
    holdMilliseconds: 500,
    transitionMilliseconds: 500,
    fps: 30,
    fullVideo: null,
    shorts: [],
    error: null,
    createdAt: null,
    updatedAt: null,
    ...overrides,
  }
}

type FetchScenario = {
  generation?: Generation
  review?: Review
  decisionReviews?: Review[]
  undoReviews?: Review[]
  nextGeneration?: Generation
  nextReview?: Review
  historyRuns?: RunHistoryEntry[]
  survivorExport?: SurvivorVideoExport | null
  startedSurvivorExport?: SurvivorVideoExport
}

function installFetch({
  generation: firstGeneration = generation(),
  review: firstReview = review({
    generationId: firstGeneration.id,
    generationNumber: firstGeneration.generationNumber,
    currentCandidate: firstGeneration.candidates[0],
  }),
  decisionReviews = [],
  undoReviews = [],
  nextGeneration,
  nextReview,
  historyRuns = [],
  survivorExport = null,
  startedSurvivorExport = survivorVideoExport({ status: 'queued' }),
}: FetchScenario = {}) {
  let currentGeneration: Generation | null = null
  let currentReview = firstReview
  let currentSurvivorExport = survivorExport
  const queuedDecisionReviews = [...decisionReviews]
  const queuedUndoReviews = [...undoReviews]

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'

    if (url === '/api/runs' && method === 'GET') {
      return jsonResponse({ runs: historyRuns })
    }

    if (url === '/api/sources' && method === 'POST') {
      return jsonResponse(uploadResponse, 201)
    }

    if (url === `/api/runs/${run.id}/generations` && method === 'POST') {
      currentGeneration = firstGeneration
      currentReview = firstReview
      return jsonResponse({ generation: firstGeneration }, 201)
    }

    if (url === `/api/runs/${run.id}/generations/current` && method === 'GET') {
      if (!currentGeneration) {
        return jsonResponse({ detail: 'Run does not have a generation yet.' }, 404)
      }
      return jsonResponse({ generation: currentGeneration })
    }

    if (url === `/api/runs/${run.id}/review/current` && method === 'GET') {
      return jsonResponse({ review: currentReview })
    }

    if (
      url === `/api/runs/${run.id}/review/thumbnails/prewarm` &&
      method === 'POST'
    ) {
      return jsonResponse(
        {
          prewarm: {
            generationId: currentReview.generationId,
            candidateCount: currentGeneration?.readyCount ?? 0,
            sizes: [256, 1024],
            status: 'queued',
          },
        },
        202,
      )
    }

    if (
      url === `/api/runs/${run.id}/exports/survivor-video` &&
      method === 'GET'
    ) {
      if (!currentSurvivorExport) {
        return jsonResponse(
          { detail: 'Run has no active ready survivors to export.' },
          409,
        )
      }
      return jsonResponse({ export: currentSurvivorExport })
    }

    if (
      url === `/api/runs/${run.id}/exports/survivor-video` &&
      method === 'POST'
    ) {
      currentSurvivorExport = startedSurvivorExport
      return jsonResponse({ export: currentSurvivorExport }, 202)
    }

    if (url === `/api/runs/${run.id}/review/decisions` && method === 'POST') {
      currentReview = queuedDecisionReviews.shift() ?? currentReview
      return jsonResponse({ review: currentReview })
    }

    if (url === `/api/runs/${run.id}/review/undo` && method === 'POST') {
      currentReview = queuedUndoReviews.shift() ?? currentReview
      return jsonResponse({ review: currentReview })
    }

    if (url === `/api/runs/${run.id}/generations/next` && method === 'POST') {
      currentGeneration = nextGeneration ?? firstGeneration
      currentReview = nextReview ?? currentReview
      return jsonResponse({ generation: currentGeneration }, 201)
    }

    return jsonResponse({ detail: `Unhandled ${method} ${url}` }, 500)
  })

  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function decisionBodies(fetchMock: ReturnType<typeof installFetch>) {
  return requestBodies(fetchMock, '/review/decisions')
}

function nextGenerationBodies(fetchMock: ReturnType<typeof installFetch>) {
  return requestBodies(fetchMock, '/generations/next')
}

function requestBodies(fetchMock: ReturnType<typeof installFetch>, path: string) {
  return fetchMock.mock.calls
    .filter(([url, init]) => String(url).includes(path) && init?.body)
    .map(([, init]) => JSON.parse(String(init?.body)))
}

function undoCallCount(fetchMock: ReturnType<typeof installFetch>) {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes('/review/undo'))
    .length
}

function exportStartCallCount(fetchMock: ReturnType<typeof installFetch>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).includes('/exports/survivor-video') &&
      (init?.method ?? 'GET') === 'POST',
  ).length
}

function prewarmCallCount(fetchMock: ReturnType<typeof installFetch>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).includes('/review/thumbnails/prewarm') &&
      (init?.method ?? 'GET') === 'POST',
  ).length
}
