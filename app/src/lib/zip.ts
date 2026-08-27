/**
 * Минимальный читатель ZIP — ровно столько, сколько нужно, чтобы открыть
 * пакет `.pmm` Процессной студии прямо в браузере.
 *
 * Отдельной библиотеки в проекте нет и заводить её ради трёх XML-частей
 * незачем: распаковку делает штатный `DecompressionStream('deflate-raw')`.
 * Идём по центральному каталогу, а не по локальным заголовкам: у записи с
 * дескриптором данных размеры в локальном заголовке нулевые, и последовательный
 * проход на таком архиве разъезжается.
 */

const SIG_EOCD = 0x06054b50
const SIG_CENTRAL = 0x02014b50
const EOCD_MIN_SIZE = 22
/** Комментарий архива по стандарту не длиннее 65 535 байт. */
const EOCD_MAX_SCAN = 0xffff + EOCD_MIN_SIZE

export interface ZipEntry {
  name: string
  data: Uint8Array
}

export class ZipError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ZipError'
  }
}

function findEocd(view: DataView): number {
  const from = Math.max(0, view.byteLength - EOCD_MAX_SCAN)
  for (let at = view.byteLength - EOCD_MIN_SIZE; at >= from; at -= 1) {
    if (view.getUint32(at, true) === SIG_EOCD) return at
  }
  throw new ZipError('Это не ZIP-архив: не найден конец центрального каталога')
}

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  if (typeof DecompressionStream === 'undefined') {
    throw new ZipError(
      'Браузер не умеет распаковывать ZIP (нет DecompressionStream). ' +
        'Откройте страницу в актуальном Chrome, Edge или Firefox.',
    )
  }
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

/** Разбирает ZIP целиком: `.pmm` — это три небольших XML, потоковость не нужна. */
export async function readZip(buffer: ArrayBuffer): Promise<ZipEntry[]> {
  const bytes = new Uint8Array(buffer)
  const view = new DataView(buffer)
  const eocd = findEocd(view)
  const count = view.getUint16(eocd + 10, true)
  let cursor = view.getUint32(eocd + 16, true)

  const decoder = new TextDecoder('utf-8')
  const entries: ZipEntry[] = []
  for (let i = 0; i < count; i += 1) {
    if (cursor + 46 > view.byteLength || view.getUint32(cursor, true) !== SIG_CENTRAL) {
      throw new ZipError('Центральный каталог ZIP повреждён')
    }
    const method = view.getUint16(cursor + 10, true)
    const compressedSize = view.getUint32(cursor + 20, true)
    const nameLength = view.getUint16(cursor + 28, true)
    const extraLength = view.getUint16(cursor + 30, true)
    const commentLength = view.getUint16(cursor + 32, true)
    const localOffset = view.getUint32(cursor + 42, true)
    const name = decoder.decode(bytes.subarray(cursor + 46, cursor + 46 + nameLength))
    cursor += 46 + nameLength + extraLength + commentLength

    // Длины имени и «extra» в локальном заголовке свои: сдвиг до данных
    // считаем по нему, а не по записи каталога.
    const localNameLength = view.getUint16(localOffset + 26, true)
    const localExtraLength = view.getUint16(localOffset + 28, true)
    const start = localOffset + 30 + localNameLength + localExtraLength
    const raw = bytes.subarray(start, start + compressedSize)

    if (name.endsWith('/')) continue
    if (method === 0) entries.push({ name, data: raw.slice() })
    else if (method === 8) entries.push({ name, data: await inflateRaw(raw) })
    else throw new ZipError(`Часть «${name}» сжата неизвестным методом ${method}`)
  }
  return entries
}

/** Текст части архива по имени; регистр и ведущий слэш роли не играют. */
export function zipText(entries: ZipEntry[], name: string): string | null {
  const want = name.replace(/^\//, '').toLowerCase()
  const hit = entries.find((e) => e.name.replace(/^\//, '').toLowerCase() === want)
  return hit ? new TextDecoder('utf-8').decode(hit.data as BufferSource) : null
}
