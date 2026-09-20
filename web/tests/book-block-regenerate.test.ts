import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'

function source(file: string): string {
  return readFileSync(path.resolve(process.cwd(), file), 'utf8')
}

test('block regeneration has one shared in-flight lock across every retry entry', () => {
  const route = source('app/(workspace)/books/BooksRoute.tsx')
  const reader = source('app/(workspace)/books/components/PageReader.tsx')
  const renderer = source('app/(workspace)/books/components/blocks/BlockRenderer.tsx')

  assert.match(route, /const regeneratingBlockIdRef = useRef/)
  assert.match(route, /regeneratingBlockIdRef\.current\) return/)
  assert.ok(reader.includes("regenerating={regeneratingBlockId === block.id}"))
  assert.ok(reader.includes("regenerateDisabled={regeneratingBlockId !== null}"))
  assert.ok(!renderer.includes("const [regenerating, setRegenerating]"))
  assert.doesNotMatch(renderer, /setRegenerating\(/)
})

test('the retry lock is released only after the regenerated page is hydrated', () => {
  const route = source('app/(workspace)/books/BooksRoute.tsx')
  const handlerStart = route.indexOf('const handleRegenerateBlock')
  const handlerEnd = route.indexOf('const handleDeleteBlock', handlerStart)
  const handler = route.slice(handlerStart, handlerEnd)

  assert.ok(handlerStart >= 0 && handlerEnd > handlerStart)
  assert.ok(
    handler.indexOf('await hydratePage(pageId)') <
      handler.indexOf('regeneratingBlockIdRef.current = null')
  )
})
