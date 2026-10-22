import { useState } from 'react';
import type { FormEvent } from 'react';
import { getClient } from '../../api/client';
import type { Recommendation, RetiredCourse } from '../../api/types';
import { errorMessage } from '../../lib/errors';
import { Dialog } from '../shell/Dialog';
import { ChatIcon, CompassIcon } from '../icons';

function Explore({ onAsk }: { onAsk: (question: string) => void }) {
  const [query, setQuery] = useState('');
  const [recommendations, setRecommendations] = useState<Recommendation[] | null>(null);
  const [retired, setRetired] = useState<RetiredCourse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const topic = query.trim();
    if (!topic || loading) return;
    setLoading(true);
    setError(null);
    setRecommendations(null);
    setRetired([]);
    try {
      const [groupsResult, retiredResult] = await Promise.allSettled([
        getClient().recommendations(topic),
        getClient().retiredCourses(topic),
      ]);
      if (groupsResult.status === 'rejected') throw groupsResult.reason;
      setRecommendations(groupsResult.value);
      setRetired(retiredResult.status === 'fulfilled' ? retiredResult.value : []);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="stack">
      <form className="search-row" onSubmit={(event) => void search(event)}>
        <label htmlFor="recommendation-query" className="visually-hidden">
          Interest or career goal
        </label>
        <input
          id="recommendation-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="e.g. neuroscience, data engineering, public health"
          autoFocus
        />
        <button type="submit" className="primary-button" disabled={loading || !query.trim()}>
          <CompassIcon size={16} />
          {loading ? 'Searching…' : 'Find'}
        </button>
      </form>
      {error && (
        <p className="callout callout-error" role="alert">
          Recommendations couldn’t be loaded. {error}
        </p>
      )}
      {recommendations &&
        (recommendations.length ? (
          <div className="recommendations">
            {recommendations.map((group) => (
              <section className="recommendation-group" key={group.clusterId}>
                <h3 className="recommendation-path">{group.path.join(' › ')}</h3>
                <ul>
                  {group.courses.map((course) => (
                    <li key={course.courseId}>
                      <span>
                        <strong>{course.code}</strong> {course.title}
                      </span>
                      <button
                        type="button"
                        className="ghost-button"
                        aria-label={`Ask about ${course.code}`}
                        onClick={() =>
                          onAsk(
                            `What should I know about ${course.code} (${course.title}), including its prerequisites?`,
                          )
                        }
                      >
                        <ChatIcon size={14} /> Ask
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
          <p className="muted">No course groups matched that goal. Try a broader topic.</p>
        ))}
      {retired.length > 0 && (
        <section className="recommendation-group retired-courses">
          <h3 className="recommendation-path">Retired courses</h3>
          <p className="muted">
            These course identities are kept for historical reference and are not currently offered.
          </p>
          <ul>
            {retired.map((course) => (
              <li key={course.courseId}>
                <span>
                  <strong>{course.code}</strong> {course.title}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function ExploreDialog({
  open,
  onClose,
  onAsk,
}: {
  open: boolean;
  onClose: () => void;
  onAsk: (question: string) => void;
}) {
  return (
    <Dialog
      open={open}
      title="Explore courses"
      description="Describe an interest or career goal to see related groups of Emory courses."
      onClose={onClose}
    >
      <Explore
        onAsk={(question) => {
          onAsk(question);
          onClose();
        }}
      />
    </Dialog>
  );
}
