/**
 * Formatting only. Nothing here computes.
 *
 * The rule that matters: **round to the precision the weakest input supports**.
 * A dose derived from one manual moisture reading is not « 1 568,3 m³ », and
 * printing it that way claims a precision the measurement does not have.
 */

/**
 * `fr-FR`, not `fr-MA`, and the reason is not cosmetic.
 *
 * `fr-MA` groups thousands with a period: « 3.936 MAD » next to « 4,78 mm/j »
 * in the same panel forces the reader to decide, digit by digit, whether a
 * period is a decimal separator or a thousands separator. On a cost figure that
 * ambiguity is worth a factor of a thousand. `fr-FR` uses a narrow no-break
 * space, which cannot be mistaken for either — and it is the exact convention
 * the Python engines use for the sentences rendered beside these figures, so
 * both halves of the product write the same number the same way.
 */
const FR = 'fr-FR'

export function volume(m3: number): string {
  return `${new Intl.NumberFormat(FR, { maximumFractionDigits: 0 }).format(m3)} m³`
}

export function depth(mm: number): string {
  return `${new Intl.NumberFormat(FR, { maximumFractionDigits: 1 }).format(mm)} mm`
}

export function rate(mmPerDay: number): string {
  return `${new Intl.NumberFormat(FR, { maximumFractionDigits: 2 }).format(mmPerDay)} mm/j`
}

export function area(ha: number): string {
  return `${new Intl.NumberFormat(FR, { maximumFractionDigits: 1 }).format(ha)} ha`
}

/**
 * One decimal: a soil moisture of 16,5 % must not read as 17 % in a list and
 * 16,5 % in the panel below it. The same value shown two ways looks like two
 * values.
 */
export function percent(value: number): string {
  return `${new Intl.NumberFormat(FR, { maximumFractionDigits: 1 }).format(value)} %`
}

export function tonnes(value: number): string {
  return `${new Intl.NumberFormat(FR, { maximumFractionDigits: 0 }).format(value)} t`
}

/** Moroccan dirham. `null` renders as a stated absence, never as zero. */
export function mad(value: number | null): string {
  if (value === null) return '—'
  return `${new Intl.NumberFormat(FR, { maximumFractionDigits: 0 }).format(value)} MAD`
}

export function dateTime(iso: string): string {
  return new Intl.DateTimeFormat(FR, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso))
}

export function dayMonth(iso: string): string {
  return new Intl.DateTimeFormat(FR, { day: '2-digit', month: 'long' }).format(new Date(iso))
}

/** « dans 14 h » / « il y a 3 h » — an operator thinks in remaining time. */
export function hoursFromNow(hours: number): string {
  const rounded = Math.round(Math.abs(hours))
  if (rounded < 1) return hours >= 0 ? 'dans moins d’une heure' : 'échéance dépassée'
  const unit = rounded > 48 ? `${Math.round(rounded / 24)} j` : `${rounded} h`
  return hours >= 0 ? `dans ${unit}` : `dépassée de ${unit}`
}
