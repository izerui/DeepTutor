import { test, expect } from '@playwright/test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { SourcesStep } from '../components/space/learning/TopicWizardSteps'

// Render the real picker with the production stylesheet. Browser wheel dispatch
// must scroll the wizard, rather than a second scroll container inside the list.
test('source picker wheel scrolls its parent wizard', async ({ page }) => {
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: {} } }, initImmediate: false })
  const markup = renderToStaticMarkup(createElement(I18nextProvider, { i18n },
    createElement(SourcesStep, {
      library: {
        knowledgeBases: Array.from({ length: 50 }, (_, index) => ({
          key: `kb-${index}`, kind: 'knowledge_base' as const, sourceId: `kb-${index}`,
          label: `Library ${index}`, detail: '', available: true,
        })),
        books: [], notebooks: [], chats: [], questionSets: [], drafts: [], partners: [], failures: [],
      },
      loading: false, selected: new Set<string>(), childLists: {}, onToggle: () => {}, onExpand: () => {},
    }),
  ))
  await page.goto('/')
  const styles = await page.locator('link[rel="stylesheet"]').evaluateAll(
    elements => elements.map(element => (element as HTMLLinkElement).href),
  )
  expect(styles.length).toBeGreaterThan(0)
  await page.setContent(`<div id="wizard" style="height:320px;width:640px;overflow:auto">${markup}<div style="height:200px">End</div></div>`)
  for (const url of styles) await page.addStyleTag({ url })
  const row = page.getByRole('button', { name: /Library 0/ }).first()
  await row.hover()
  await page.mouse.wheel(0, 400)
  await expect.poll(() => page.locator('#wizard').evaluate(element => element.scrollTop)).toBeGreaterThan(0)
})
