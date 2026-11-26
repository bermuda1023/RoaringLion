import { useQuery } from '@tanstack/react-query';
import { api, type RelatedProgramme } from '../api/client';

/** Programme switcher for Structuring Studio. Options come from the renewal-chain query. */
export function RelatedProgrammeSelect({
  programmeId,
  current,
  onChange,
}: {
  programmeId: string;
  current: RelatedProgramme;
  onChange: (id: string) => void;
}) {
  const relatedQ = useQuery({
    queryKey: ['relatedProgrammes', programmeId],
    queryFn: () => api.listRelatedProgrammes(programmeId),
  });
  const options = relatedQ.data && relatedQ.data.length > 0 ? relatedQ.data : [current];
  return (
    <select
      aria-label="Linked programmes"
      value={programmeId}
      onChange={(event) => onChange(event.target.value)}
      style={{ maxWidth: '100%' }}
    >
      {options.map((programme) => (
        <option key={programme.programmeId} value={programme.programmeId}>
          {programme.cedentName} — {programme.programmeName} ({programme.renewalPeriod})
        </option>
      ))}
    </select>
  );
}
