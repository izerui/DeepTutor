/**
 * The server's English messages are i18n keys. A few carry a value (a title)
 * inside them; those are matched here and translated with the value, so the
 * key stays stable while the reader sees their own language.
 */
type Translate = (key: string, values?: Record<string, unknown>) => string;

const PRIVATE_COURSE_BOOK =
  /^Make the book “([\s\S]*)” public first: learners of this course cannot open it\.$/;

export function serverMessage(message: string, t: Translate): string {
  const privateBook = PRIVATE_COURSE_BOOK.exec(message);
  if (privateBook) {
    return t(
      "Make the book “{{title}}” public first: learners of this course cannot open it.",
      { title: privateBook[1] },
    );
  }
  return t(message);
}
