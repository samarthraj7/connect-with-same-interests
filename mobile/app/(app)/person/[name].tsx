import { useLocalSearchParams, useRouter } from "expo-router";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ScreenBackdrop } from "../../../components/ScreenBackdrop";
import { Body, Bullet, Button, Field, SectionTitle, UrlLink } from "../../../components/ui";
import { api } from "../../../lib/api";
import { colors, fonts, space } from "../../../lib/theme";

/**
 * Person briefing layout (what the user sees):
 * 1. Who they are — short bio
 * 2. Things to talk about — topics + hooks (overlap stays invisible)
 * 3. Openers / deeper questions
 * 4. Full dossier — career, personal, presence, network, social
 * 5. Contact + CRM
 */

function ListSection({ title, items }: { title: string; items?: any[] }) {
  if (!items || !items.length) return null;
  return (
    <>
      <SectionTitle>{title}</SectionTitle>
      {items.map((item, i) => {
        if (typeof item === "string") return <Bullet key={`${title}-${i}`}>{item}</Bullet>;
        if (item?.name) {
          const line = [item.name, item.title, item.context].filter(Boolean).join(" — ");
          return <Bullet key={`${title}-${i}`}>{line}</Bullet>;
        }
        if (item?.topic) {
          const source = item.source ? String(item.source) : "";
          const sourceIsUrl = /^https?:\/\//i.test(source);
          const bits = [item.topic, !sourceIsUrl && source && `[${source}]`, item.snippet || item.evidence]
            .filter(Boolean)
            .join(" · ");
          return (
            <View key={`${title}-${i}`}>
              <Bullet>{bits}</Bullet>
              {sourceIsUrl ? (
                <View style={{ marginLeft: 16, marginTop: -4, marginBottom: 8 }}>
                  <UrlLink url={source} label="source" />
                </View>
              ) : null}
            </View>
          );
        }
        return <Bullet key={`${title}-${i}`}>{JSON.stringify(item)}</Bullet>;
      })}
    </>
  );
}

function Fact({ label, value }: { label: string; value?: any }) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  if (Array.isArray(value)) {
    return (
      <View style={styles.factBlock}>
        <Text style={styles.factLabel}>{label}</Text>
        {value.map((v, i) => (
          <Bullet key={`${label}-${i}`}>{typeof v === "string" ? v : JSON.stringify(v)}</Bullet>
        ))}
      </View>
    );
  }
  const str = String(value);
  const looksLikeUrl = /^https?:\/\//i.test(str.trim());
  return (
    <View style={styles.factBlock}>
      <Text style={styles.factLabel}>{label}</Text>
      {looksLikeUrl ? <UrlLink url={str.trim()} label={`Open ${label}`} /> : <Body>{str}</Body>}
    </View>
  );
}

function SocialBlock({ label, data }: { label: string; data?: any }) {
  if (!data || typeof data !== "object") return null;
  const status = data.status;
  if (!status || status === "skipped") return null;
  const profile = data.profile || {};
  const handle = data.handle || profile.username || profile.full_name;
  const url = data.profile_url;
  const posts = data.recent_posts || [];
  const face = data.face_match;
  const rankings = (face?.rankings || []).slice(0, 4);
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{label}</Text>
      <Text style={styles.cardMeta}>
        {status}
        {data.match_confidence ? ` · ${data.match_confidence} match` : ""}
        {face?.match_mode ? ` · face ${face.match_mode}` : ""}
      </Text>
      {handle ? <Text style={styles.cardMeta}>@{String(handle).replace(/^@/, "")}</Text> : null}
      {url ? <UrlLink url={url} label={`Open ${label}`} /> : null}
      {(profile.biography || profile.bio) ? (
        <Text style={styles.cardBody}>{profile.biography || profile.bio}</Text>
      ) : null}
      {face?.accepted ? (
        <Text style={styles.cardMeta}>
          Face match: @{face.accepted.handle} ({face.accepted.score}/100)
        </Text>
      ) : null}
      {status === "ambiguous" && rankings.length ? (
        <>
          <Text style={[styles.cardMeta, { marginTop: 8 }]}>Probable accounts (face ranked)</Text>
          {rankings.map((r: any, i: number) => (
            <Bullet key={i}>
              @{r.handle}
              {r.score != null ? ` · ${r.score}/100` : ""}
              {r.reason ? ` — ${r.reason}` : ""}
            </Bullet>
          ))}
        </>
      ) : null}
      {posts.slice(0, 4).map((p: any, i: number) => (
        <Bullet key={i}>{(p.caption || p.snippet || "").slice(0, 160)}</Bullet>
      ))}
    </View>
  );
}

export default function PersonDetail() {
  const { name, company, draftId, needsRating, linkedin: linkedinParam } = useLocalSearchParams<{
    name: string;
    company?: string;
    draftId?: string;
    needsRating?: string;
    linkedin?: string;
  }>();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [factDraft, setFactDraft] = useState("");
  const [pendingDraftId, setPendingDraftId] = useState<string | null>(
    draftId ? String(draftId) : null,
  );
  const [ratingNotes, setRatingNotes] = useState("");
  const [ratingBusy, setRatingBusy] = useState(false);
  const [showBadForm, setShowBadForm] = useState(false);
  const [pickedLinkedin, setPickedLinkedin] = useState<string>(
    linkedinParam ? String(linkedinParam) : "",
  );
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState("");
  const [chatMessages, setChatMessages] = useState<{ role: "user" | "assistant"; content: string }[]>(
    [],
  );

  useEffect(() => {
    if (!name) return;
    (async () => {
      try {
        if (draftId) {
          const draft = await api.researchDraft(String(draftId));
          const li =
            draft.linkedin_url ||
            draft.contact?.linkedin_url ||
            (linkedinParam ? String(linkedinParam) : "") ||
            "";
          if (li) setPickedLinkedin(li);
          setData({
            name: draft.name || name,
            company: draft.company || company || "",
            summary: draft.summary,
            conversation: draft.conversation,
            mutuals: draft.mutuals,
            in_your_network: draft.in_your_network,
            needs_rating: true,
            draft_id: draft.draft_id,
            linkedin_url: li,
            contact: { ...(draft.contact || {}), ...(li ? { linkedin_url: li } : {}) },
            sources: draft.sources || {},
          });
          setPendingDraftId(draft.draft_id);
          return;
        }
        const res = await api.person(String(name), company || null);
        const li =
          res?.contact?.linkedin_url ||
          res?.sources?.exa_search?.linkedin_url ||
          (linkedinParam ? String(linkedinParam) : "") ||
          "";
        if (li) setPickedLinkedin(li);
        setData(res);
      } catch (e: any) {
        setError(e.message || "Failed to load");
      }
    })();
  }, [name, company, draftId, linkedinParam]);

  const submitRating = async (rating: "good" | "bad") => {
    if (!pendingDraftId) return;
    if (rating === "bad" && !showBadForm) {
      setShowBadForm(true);
      return;
    }
    if (rating === "bad" && !ratingNotes.trim()) {
      setError("Tell us what was wrong so re-research can fix it.");
      return;
    }
    setRatingBusy(true);
    setError("");
    try {
      const res = await api.researchFeedback({
        draft_id: pendingDraftId,
        rating,
        wrong_notes: rating === "bad" ? ratingNotes.trim() || undefined : undefined,
        wrong_categories: rating === "bad" ? ["quality"] : undefined,
        auto_retry: false,
      });
      setShowBadForm(false);
      if (rating === "good" && res.committed) {
        setPendingDraftId(null);
        const li = res.linkedin_url || res.contact?.linkedin_url || pickedLinkedin;
        if (li) setPickedLinkedin(li);
        setData((d: any) => ({
          ...d,
          ...res,
          needs_rating: false,
          summary: res.summary || d?.summary,
          conversation: res.conversation || d?.conversation,
          contact: { ...(res.contact || d?.contact || {}), ...(li ? { linkedin_url: li } : {}) },
          linkedin_url: li,
        }));
        return;
      }

      // Bad → re-research same identity with stored corrections
      setPendingDraftId(null);
      setError("Re-researching with your corrections…");
      const researched = await api.research({
        name: res.name || name,
        company: res.company || company || null,
        university: res.university || null,
        linkedin_url: res.linkedin_url || pickedLinkedin || null,
        force_refresh: true,
        auto_commit: false,
      });
      if (researched.needs_rating && researched.draft_id) {
        setPendingDraftId(researched.draft_id);
        setRatingNotes("");
        setData({
          name: researched.name || name,
          company: researched.company || company || "",
          summary: researched.summary,
          conversation: researched.conversation,
          mutuals: researched.mutuals,
          in_your_network: researched.in_your_network,
          needs_rating: true,
          draft_id: researched.draft_id,
          linkedin_url: researched.linkedin_url || pickedLinkedin,
          contact: researched.contact || {},
          sources: researched.sources || {},
        });
        setError("New draft ready — review again.");
        return;
      }
      setError(res.message || "Marked as bad — not saved.");
      setTimeout(() => router.back(), 1600);
    } catch (e: any) {
      setError(e.message || "Could not save rating");
    } finally {
      setRatingBusy(false);
    }
  };

  if (!data && !error) {
    return (
      <ScreenBackdrop>
        <View style={{ flex: 1, justifyContent: "center" }}>
          <ActivityIndicator color={colors.forest} />
        </View>
      </ScreenBackdrop>
    );
  }

  const summary = data?.summary || {};
  const conv =
    data?.conversation?.status === "ok"
      ? data.conversation
      : summary.conversation?.status === "ok"
        ? summary.conversation
        : data?.conversation || {};
  const talkAbout = conv.talk_about || [];
  const openers = conv.openers || summary.conversation_starters || [];
  const deep = conv.deep_questions || summary.deep_dive_questions || [];
  const related = conv.related_topics || [];
  const personal = summary.personal_info || data?.sources?.personal_info || {};
  const presence = summary.public_presence || {};
  const seniors = summary.senior_connections || [];
  const collaborators = summary.research_collaborators || [];
  const contact = data?.contact || {};
  const linkedin =
    pickedLinkedin ||
    contact.linkedin_url ||
    data?.linkedin_url ||
    data?.sources?.exa_search?.linkedin_url ||
    data?.sources?.gemini_search?.linkedin_url ||
    data?.sources?.linkedin_public?.profile_url ||
    (linkedinParam ? String(linkedinParam) : "");
  const sources = data?.sources || {};
  const hasConversation = talkAbout.length > 0 || openers.length > 0 || !!conv.conversation_brief;

  const saveNote = async () => {
    if (!note.trim() || !name) return;
    setSaving(true);
    try {
      await api.addNote(String(name), note.trim(), company || null);
      setNote("");
      const res = await api.person(String(name), company || null);
      setData(res);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const askChat = async () => {
    const q = chatInput.trim();
    if (!q || !name || chatBusy) return;
    setChatError("");
    setChatBusy(true);
    const nextHistory = [...chatMessages, { role: "user" as const, content: q }];
    setChatMessages(nextHistory);
    setChatInput("");
    try {
      const res = await api.personChat(String(name), {
        question: q,
        company: company || null,
        draft_id: pendingDraftId || null,
        history: nextHistory.slice(0, -1),
      });
      setChatMessages([...nextHistory, { role: "assistant", content: res.answer }]);
    } catch (e: any) {
      setChatError(e.message || "Chat failed");
      setChatMessages(nextHistory);
    } finally {
      setChatBusy(false);
    }
  };

  return (
    <ScreenBackdrop>
      <SafeAreaView style={{ flex: 1 }} edges={["top"]}>
        <ScrollView contentContainerStyle={styles.pad}>
          <Button title="← Back" variant="ghost" onPress={() => router.back()} style={{ alignSelf: "flex-start", marginBottom: 8 }} />
          <Text style={styles.name}>{data?.name || name}</Text>
          {data?.company ? <Text style={styles.company}>{data.company}</Text> : null}
          {summary.identity_confidence ? (
            <Text style={styles.meta}>Identity: {summary.identity_confidence}</Text>
          ) : null}
          {error ? <Text style={styles.err}>{error}</Text> : null}

          {pendingDraftId || needsRating === "1" || data?.needs_rating ? (
            <View style={styles.rateBox}>
              <Text style={styles.rateTitle}>Is this research good?</Text>
              <Text style={styles.cardMeta}>
                Good saves it to your people DB. Bad stores what was wrong and re-researches with those corrections.
              </Text>
              {showBadForm ? (
                <>
                  <Field
                    label="What was wrong?"
                    value={ratingNotes}
                    onChangeText={setRatingNotes}
                    placeholder="Wrong person, wrong company, outdated role, mixed another Samarth…"
                    multiline
                  />
                  <Button
                    title="Fix & re-research"
                    variant="ember"
                    loading={ratingBusy}
                    onPress={() => submitRating("bad")}
                    style={{ marginBottom: 8 }}
                  />
                  <Button title="Cancel" variant="ghost" onPress={() => setShowBadForm(false)} />
                </>
              ) : (
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
                  <Button
                    title="Good — save"
                    variant="ember"
                    loading={ratingBusy}
                    onPress={() => submitRating("good")}
                    style={{ flex: 1 }}
                  />
                  <Button
                    title="Bad"
                    variant="ghost"
                    loading={ratingBusy}
                    onPress={() => submitRating("bad")}
                    style={{ flex: 1 }}
                  />
                </View>
              )}
            </View>
          ) : null}

          <Button
            title={refreshing ? "Refreshing…" : "Refresh (stale sources)"}
            variant="ghost"
            loading={refreshing}
            onPress={async () => {
              setRefreshing(true);
              setError("");
              try {
                const res = await api.refreshPerson(String(name), company || null);
                const next = await api.person(String(name), company || null);
                setData({ ...next, whats_new: res.whats_new || next.whats_new });
              } catch (e: any) {
                setError(e.message);
              } finally {
                setRefreshing(false);
              }
            }}
            style={{ marginBottom: 8 }}
          />

          {(data?.whats_new?.changes || []).length ? (
            <>
              <SectionTitle>What’s new</SectionTitle>
              {(data.whats_new.changes as any[]).map((c, i) => (
                <Bullet key={i}>
                  {c.detail || c.type}
                  {c.source ? ` (${c.source})` : ""}
                  {c.snippet ? ` — ${c.snippet}` : ""}
                </Bullet>
              ))}
            </>
          ) : null}

          {(data?.mutuals || []).length ? (
            <>
              <SectionTitle>People you may both know</SectionTitle>
              <Body>From your LinkedIn connections export — only when evidence matches.</Body>
              {(data.mutuals as any[]).map((m, i) => (
                <Bullet key={i}>
                  {m.name}
                  {m.company ? ` · ${m.company}` : ""}
                </Bullet>
              ))}
            </>
          ) : null}
          {data?.in_your_network ? (
            <Text style={styles.meta}>
              In your LinkedIn network
              {data.in_your_network.connected_on ? ` · connected ${data.in_your_network.connected_on}` : ""}
            </Text>
          ) : null}

          {/* 1. Who they are */}
          {summary.summary ? (
            <>
              <SectionTitle>Who they are</SectionTitle>
              <Body>{summary.summary}</Body>
            </>
          ) : null}
          {summary.identity_notes ? (
            <Text style={[styles.cardMeta, { marginTop: 8 }]}>{summary.identity_notes}</Text>
          ) : null}
          {(data?.conflicts || summary.conflicts || []).length ? (
            <>
              <SectionTitle>Needs review</SectionTitle>
              <Body>New evidence conflicted with verified facts — not auto-merged.</Body>
              {(data?.conflicts || summary.conflicts || []).map((c: any, i: number) => (
                <Bullet key={i}>
                  {c.predicate || "conflict"}: {String(c.existing_object || c.existing || "")}
                  {" vs "}
                  {String(c.new_object || c.incoming || "")}
                </Bullet>
              ))}
            </>
          ) : null}

          {/* 2. Conversation fuel — overlap is invisible */}
          {hasConversation ? (
            <>
              <SectionTitle>Things to talk about</SectionTitle>
              {conv.conversation_brief ? <Body>{conv.conversation_brief}</Body> : null}
              {talkAbout.map((t: any, i: number) => (
                <View key={i} style={styles.card}>
                  <Text style={styles.cardTitle}>{t.topic || "Topic"}</Text>
                  {t.hook ? <Text style={styles.cardBody}>{t.hook}</Text> : null}
                </View>
              ))}
              {related.length ? (
                <View style={{ marginTop: 8 }}>
                  <Text style={styles.factLabel}>Also worth exploring</Text>
                  {related.map((r: string) => (
                    <Bullet key={r}>{r}</Bullet>
                  ))}
                </View>
              ) : null}
            </>
          ) : null}

          {openers.length ? (
            <>
              <SectionTitle>Openers</SectionTitle>
              {openers.map((s: string) => (
                <Bullet key={s}>{s}</Bullet>
              ))}
            </>
          ) : null}

          {deep.length ? (
            <>
              <SectionTitle>Go deeper</SectionTitle>
              {deep.map((s: string) => (
                <Bullet key={s}>{s}</Bullet>
              ))}
            </>
          ) : null}

          <SectionTitle>Ask about them</SectionTitle>
          <Body>
            Chat about this dossier and conversation ideas. Answers stay grounded in what we researched.
          </Body>
          {chatMessages.map((m, i) => (
            <View
              key={`${m.role}-${i}`}
              style={[
                styles.card,
                m.role === "user" ? { borderColor: colors.forest } : null,
              ]}
            >
              <Text style={styles.cardMeta}>{m.role === "user" ? "You" : "Connect Deeply"}</Text>
              <Text style={styles.cardBody}>{m.content}</Text>
            </View>
          ))}
          {chatError ? <Text style={styles.err}>{chatError}</Text> : null}
          <Field
            label="Question"
            value={chatInput}
            onChangeText={setChatInput}
            placeholder="What should I open with? Any shared cities?"
            multiline
          />
          <Button title={chatBusy ? "Thinking…" : "Ask"} onPress={askChat} loading={chatBusy} style={{ marginBottom: 8 }} />

          {/* 3. Full dossier */}
          <SectionTitle>Career</SectionTitle>
          {(summary.career_history || []).length ? (
            (summary.career_history || []).map((c: string, i: number) => <Bullet key={i}>{c}</Bullet>)
          ) : (
            <Body>No public career history found.</Body>
          )}

          <ListSection title="Interests" items={summary.interests} />
          <ListSection title="Notable points" items={summary.notable_points} />
          <ListSection title="Affiliations" items={summary.notable_affiliations} />
          <ListSection title="Awards & recognition" items={summary.awards_and_recognitions} />

          {collaborators.length ? (
            <>
              <SectionTitle>Collaborators</SectionTitle>
              {collaborators.map((c: any, i: number) => (
                <Bullet key={i}>
                  {c.name}
                  {c.context ? ` — ${c.context}` : ""}
                </Bullet>
              ))}
            </>
          ) : null}

          <SectionTitle>Personal</SectionTitle>
          <Fact label="Born / hometown" value={personal.born_or_hometown} />
          <Fact label="Birthplace note" value={!personal.born_or_hometown ? personal.birthplace_note : null} />
          <Fact label="Raised in" value={personal.raised_in} />
          <Fact label="Lives now" value={personal.current_location} />
          <Fact label="Also lived in" value={personal.lived_in} />
          <Fact label="Hobbies" value={personal.hobbies} />
          <Fact label="Sports" value={personal.sports_interests} />
          <Fact label="Weekends" value={personal.weekend_preferences} />
          <Fact label="Family" value={personal.family_background} />
          <Fact label="Spouse / partner" value={personal.spouse || summary.family?.spouse} />
          {(personal.children || summary.family?.children || []).length ? (
            <ListSection
              title="Children"
              items={(personal.children || summary.family?.children || []).map((c: any) =>
                typeof c === "string"
                  ? c
                  : [c.name, c.school, c.company, c.note].filter(Boolean).join(" — "),
              )}
            />
          ) : null}
          {(personal.siblings || summary.family?.siblings || []).length ? (
            <ListSection
              title="Siblings"
              items={(personal.siblings || summary.family?.siblings || []).map((c: any) =>
                typeof c === "string" ? c : [c.name, c.note].filter(Boolean).join(" — "),
              )}
            />
          ) : null}
          <Fact
            label="Estimated age"
            value={
              personal.estimated_age_band || summary.estimated_age_band
                ? `${personal.estimated_age_band || summary.estimated_age_band}${
                    personal.estimated_age_basis || summary.estimated_age_basis
                      ? ` (${personal.estimated_age_basis || summary.estimated_age_basis})`
                      : ""
                  }`
                : null
            }
          />
          <Fact label="Notes" value={personal.personal_notes} />

          <SectionTitle>Public writing & posts</SectionTitle>
          <Fact label="Themes" value={presence.posts_about} />
          {(presence.recent_posts_or_writing || []).map((item: any, i: number) => (
            <View key={i} style={styles.card}>
              <Text style={styles.cardTitle}>{item.topic || "(untitled)"}</Text>
              {item.source ? (
                /^https?:\/\//i.test(String(item.source)) ? (
                  <UrlLink url={String(item.source)} label="source" />
                ) : (
                  <Text style={styles.cardMeta}>{item.source}</Text>
                )
              ) : null}
              {item.snippet ? <Body>{String(item.snippet)}</Body> : null}
            </View>
          ))}
          <ListSection title="Liked / engaged with" items={presence.liked_or_engaged_with} />
          {presence.availability_note ? <Body>{presence.availability_note}</Body> : null}

          {seniors.length ? (
            <>
              <SectionTitle>Senior connections</SectionTitle>
              {seniors.map((p: any, i: number) => (
                <View key={i} style={styles.card}>
                  <Text style={styles.cardTitle}>
                    {p.name}
                    {p.title ? ` — ${p.title}` : ""}
                    {p.seniority ? ` (${p.seniority})` : ""}
                  </Text>
                  {p.context ? <Text style={styles.cardBody}>{p.context}</Text> : null}
                </View>
              ))}
            </>
          ) : null}

          {(sources.instagram_public || sources.facebook_public || sources.twitter_public) && (
            <>
              <SectionTitle>Social</SectionTitle>
              <SocialBlock label="Instagram" data={sources.instagram_public} />
              <SocialBlock label="Facebook" data={sources.facebook_public} />
              <SocialBlock label="Twitter / X" data={sources.twitter_public} />
            </>
          )}

          {/* 4. Contact + CRM */}
          <SectionTitle>Reach out</SectionTitle>
          {linkedin ? (
            <>
              <Text style={styles.factLabel}>LinkedIn</Text>
              <UrlLink url={linkedin} label="Open LinkedIn profile" />
            </>
          ) : (
            <Body>No LinkedIn URL found yet.</Body>
          )}
          <Fact label="Email" value={contact.email} />
          <Fact label="Phone" value={contact.phone} />
          <Fact label="GitHub" value={contact.github_username} />
          {conv.message_angle ? <Body>{`Suggested note: ${conv.message_angle}`}</Body> : null}

          <SectionTitle>Your notes</SectionTitle>
          <Field label="Note" value={note} onChangeText={setNote} placeholder="Met at TiE, follow up next week…" />
          <Button title="Save note" onPress={saveNote} loading={saving} />
          {(data?.interactions || [])
            .filter((x: any) => x.type === "note")
            .slice()
            .reverse()
            .map((x: any, i: number) => (
              <Bullet key={i}>
                {x.note}
                {x.at ? ` (${String(x.at).slice(0, 10)})` : ""}
              </Bullet>
            ))}

          <SectionTitle>Pending facts</SectionTitle>
          <Body>Claims you add wait for corroboration unless you mark them as personal knowledge.</Body>
          <Field label="Claim" value={factDraft} onChangeText={setFactDraft} placeholder="They founded X in 2019…" />
          <Button
            title="Add as pending"
            onPress={async () => {
              if (!factDraft.trim()) return;
              await api.addPendingFact({
                claim: factDraft.trim(),
                person_name: String(name),
                person_company: company || null,
              });
              setFactDraft("");
              const res = await api.person(String(name), company || null);
              setData(res);
            }}
            style={{ marginBottom: 8 }}
          />
          <Button
            title="I know this personally"
            variant="ghost"
            onPress={async () => {
              if (!factDraft.trim()) return;
              await api.addPendingFact({
                claim: factDraft.trim(),
                person_name: String(name),
                person_company: company || null,
                trusted_personal: true,
              });
              setFactDraft("");
              const res = await api.person(String(name), company || null);
              setData(res);
            }}
          />
          {(data?.pending_facts || []).map((f: any) => (
            <Bullet key={f.id}>
              [{f.status}] {f.claim}
            </Bullet>
          ))}
        </ScrollView>
      </SafeAreaView>
    </ScreenBackdrop>
  );
}

const styles = StyleSheet.create({
  pad: { padding: space.lg, paddingBottom: 56 },
  name: { fontFamily: fonts.display, fontSize: 34, color: colors.ink, letterSpacing: -0.6 },
  company: { fontFamily: fonts.body, color: colors.muted, marginBottom: 4 },
  meta: { fontFamily: fonts.body, color: colors.leaf, marginBottom: space.md, fontSize: 13 },
  err: { color: colors.danger, fontFamily: fonts.bodyMed, marginBottom: 8 },
  rateBox: {
    marginBottom: 14,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: colors.ember,
    backgroundColor: "rgba(251,252,250,0.95)",
  },
  rateTitle: { fontFamily: fonts.bodySemi, color: colors.ink, marginBottom: 6, fontSize: 16 },
  factBlock: { marginBottom: 10 },
  factLabel: {
    fontFamily: fonts.bodyMed,
    fontSize: 12,
    color: colors.moss,
    textTransform: "uppercase",
    letterSpacing: 0.7,
    marginBottom: 4,
  },
  card: {
    marginTop: 10,
    padding: 12,
    borderRadius: 14,
    backgroundColor: "rgba(251,252,250,0.85)",
    borderWidth: 1,
    borderColor: colors.line,
  },
  cardTitle: { fontFamily: fonts.bodySemi, color: colors.ink, marginBottom: 4 },
  cardMeta: { fontFamily: fonts.body, color: colors.muted, fontSize: 13, lineHeight: 18 },
  cardBody: { fontFamily: fonts.body, color: colors.ink, fontSize: 14, lineHeight: 20 },
  link: { fontFamily: fonts.bodyMed, color: colors.ember, fontSize: 13, marginTop: 4 },
  outreach: {
    marginTop: 12,
    fontFamily: fonts.body,
    fontStyle: "italic",
    color: colors.ink,
    lineHeight: 21,
  },
});
