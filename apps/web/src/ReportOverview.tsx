import type { Baseline, Clause, Finding, Report, Score } from './types';

export type ReportGroup = 'all' | 'attention' | 'favorable' | 'neutral' | 'unrated';
export function inGroup(clause: Clause, group: ReportGroup) {
  if (group === 'unrated') return clause.findings.length === 0;
  if (group === 'all') return true;
  return clause.findings.some(f => group === 'attention' ? f.severity >= 2 : f.rating === group);
}

export default function ReportOverview({ report, shown, score, onGroup, onEvidence }: {
  report: Report; shown: Baseline; score: Score;
  onGroup: (group: ReportGroup) => void; onEvidence: (finding: Finding) => void;
}) {
  const clauses = shown.clauses.filter(c => c.substantive);
  const rated = clauses.filter(c => c.findings.length).length;
  const count = (group: ReportGroup) => clauses.filter(c => inGroup(c, group)).length;
  const findings = clauses.flatMap(c => c.findings.map(f => ({ finding: f, clause: c.id })))
    .sort((a, b) => b.finding.severity - a.finding.severity || a.clause - b.clause);
  const contextLimited = clauses.filter(c => c.unrated_reason === 'adequate_context_does_not_fit').length;
  const highlights = (positive: boolean) => findings.filter(({ finding: f }) => positive ? f.rating === 'favorable' : f.severity >= 2).slice(0, 3);

  return <>
    <section className="report-overview" aria-label="Policy overview">
      <div className={`score-panel ${!rated ? 'score-unresolved' : ''}`}>
        <p className="eyebrow">Policy score · higher is more favorable</p>
        {rated ? <><strong className="score">{score.worst === score.best ? score.best : `${score.worst}–${score.best}`}<small> / 100</small></strong>
          <span className="grade-label">{score.grade ? `Grade ${score.grade}` : 'Provisional range · no single grade yet'}</span></>
          : <><strong className="empty-score">Not enough evidence</strong><span>No supported score yet</span></>}
        <p>{rated ? 'This range includes uncertainty about unresolved clauses.' : 'No clauses received a supported rating. This does not mean the policy is safe.'}</p>
        <details className="score-explanation"><summary>How to read this score</summary>
          <p>Score range: {score.worst}–{score.best} / 100. Higher is more favorable. Severity runs from 0 to 5; higher severity means more concern.</p>
          <p>A letter grade requires at least 80% clause coverage, consistent policy scope, and both bounds in the same grade band. This is not a legal verdict or an accuracy estimate.</p>
        </details>
      </div>
      <div className="overview-context">
        <p className="eyebrow">Your first look</p>
        <h3>{!rated ? 'Read with caution: the review is incomplete.' : count('attention') ? `${count('attention')} ${count('attention') === 1 ? 'clause needs' : 'clauses need'} attention.` : 'No concerning findings in the rated clauses.'}</h3>
        <p>{!rated ? 'The request finished, but it did not produce supported findings. Review the source before drawing a conclusion.' : 'Start with the findings below, check their exact wording, then review what is still unknown.'}</p>
        <div className="coverage-heading"><strong>Evidence coverage</strong><span>{Math.round(score.coverage * 100)}%</span></div>
        <progress max="100" value={score.coverage * 100} aria-label="Evidence coverage" />
        <p className="muted">{rated} of {clauses.length} substantive clauses rated. {clauses.length - rated} still unresolved.</p>
        {!score.scope_consistent && <p className="error" role="alert">Some findings fall outside the selected policy type. Check the policy type before interpreting a grade.</p>}
        {report.review_status === 'limited' && <p className="review-limitation" role="status"><strong>AI review was limited.</strong> {contextLimited
          ? `${contextLimited} ${contextLimited === 1 ? 'clause needed' : 'clauses needed'} more context than the review budget could fit. A shorter complete policy section, with its definitions and exceptions, may be reviewable.`
          : 'Some clauses exceeded the review budget and remain unresolved.'} No additional review happens automatically.</p>}
      </div>
    </section>
    <div className="highlights" aria-label="What stands out">
      {[{ positive: false, title: 'Needs attention', group: 'attention' as const }, { positive: true, title: 'Favorable findings', group: 'favorable' as const }].map(({ positive, title, group }) =>
        <section className={`highlight-panel ${positive ? 'positive-panel' : 'concern-panel'}`} key={group}>
          <div className="section-heading"><h3>{title}</h3><span className="count-badge">{count(group)} clauses</span></div>
          {highlights(positive).length ? <ul className="highlight-list">{highlights(positive).map(({ finding: f, clause }) => <li key={`${clause}:${f.category}:${f.rule_id}`}>
            <span className={`rating ${f.rating}`}>{f.rating} · {f.severity}/5</span>
            <h4>{shown.categories.find(c => c.id === f.category)?.name || `Category ${f.category}`}</h4>
            <p>{f.explanation}</p><button type="button" className="text-button" onClick={() => onEvidence(f)}>Inspect evidence · clause {clause}</button>
          </li>)}</ul> : <p className="empty-state">{positive ? 'No favorable clauses were rated. Unknown clauses may still contain protections.' : 'No concerning clauses were rated. Unresolved wording still needs your attention.'}</p>}
          {count(group) > 0 && <button type="button" onClick={() => onGroup(group)}>View all {positive ? 'favorable findings' : 'concerns'}</button>}
        </section>)}
      <section className="unresolved-strip"><div><h3>What is still unclear?</h3><p>{count('unrated')} {count('unrated') === 1 ? 'clause has' : 'clauses have'} no supported rating. Unrated does not mean harmless; neutral findings are listed separately.</p></div>
        <button type="button" onClick={() => onGroup('unrated')}>Review unresolved clauses</button>
      </section>
    </div>
  </>;
}
