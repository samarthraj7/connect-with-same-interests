import React, { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { api, API_BASE } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fonts, space } from "../lib/theme";
import { useTheme } from "../lib/theme-context";
import { ResearchBriefingPreview } from "./ResearchBriefingPreview";
import { Body, Button, ChipInput, Field } from "./ui";

type Props = { visible: boolean; onClose: () => void };

type Candidate = {
  name: string;
  role?: string;
  company?: string;
  location?: string;
  linkedin_url?: string;
  photo_url?: string;
  context?: string;
};

const TIP_CHIPS = [
  { label: "Hiking", kind: "hobbies" as const },
  { label: "Cooking", kind: "hobbies" as const },
  { label: "Startups", kind: "interests" as const },
  { label: "Design", kind: "interests" as const },
  { label: "Soccer", kind: "sports" as const },
  { label: "Tennis", kind: "sports" as const },
];

function CandidateAvatar({
  name,
  photoUrl,
  mist,
  forest,
}: {
  name: string;
  photoUrl?: string;
  mist: string;
  forest: string;
}) {
  const [broken, setBroken] = useState(false);
  const showImg = !!photoUrl && !broken;
  if (showImg) {
    return (
      <Image
        source={{ uri: photoUrl }}
        onError={() => setBroken(true)}
        style={{ width: 48, height: 48, borderRadius: 24, backgroundColor: mist }}
      />
    );
  }
  return (
    <View
      style={{
        width: 48,
        height: 48,
        borderRadius: 24,
        backgroundColor: mist,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Text style={{ fontFamily: fonts.bodySemi, color: forest, fontSize: 16 }}>
        {(name || "?")[0].toUpperCase()}
      </Text>
    </View>
  );
}

/**
 * Signup flow:
 * 0 Name → 1 Find Me → 2 Research wait → 3 Confirm/edit → 4 Email OTP → 5 Password + create
 */
export function SignupSheet({ visible, onClose }: Props) {
  const { colors } = useTheme();
  const { setSession, refresh } = useAuth();
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("");
  const [showFilters, setShowFilters] = useState(false);

  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [university, setUniversity] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [matchMode, setMatchMode] = useState<"exact" | "probable_only" | "none" | "">("");
  const [matchMessage, setMatchMessage] = useState("");
  const [picked, setPicked] = useState<Candidate | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [emailVerifiedToken, setEmailVerifiedToken] = useState<string | null>(null);
  const [otpHint, setOtpHint] = useState("");
  const [debugCode, setDebugCode] = useState<string | null>(null);
  const [hobbies, setHobbies] = useState<string[]>([]);
  const [interests, setInterests] = useState<string[]>([]);
  const [sports, setSports] = useState<string[]>([]);
  const [headline, setHeadline] = useState("");
  const [draftId, setDraftId] = useState<string | null>(null);
  const [briefing, setBriefing] = useState<Record<string, any> | null>(null);
  const [ratingNotes, setRatingNotes] = useState("");
  const [showBadForm, setShowBadForm] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const resetResearchUi = () => {
    setProgress(0);
    setPhase("");
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => {
    if (!visible) {
      setStep(0);
      setError("");
      resetResearchUi();
      setCandidates([]);
      setMatchMode("");
      setMatchMessage("");
      setPicked(null);
      setShowFilters(false);
      setDraftId(null);
      setBriefing(null);
      setHeadline("");
      setHobbies([]);
      setInterests([]);
      setSports([]);
      setEmail("");
      setPassword("");
      setOtpCode("");
      setEmailVerifiedToken(null);
      setOtpHint("");
      setDebugCode(null);
      setCompany("");
      setUniversity("");
      setLinkedin("");
      setName("");
      setRatingNotes("");
      setShowBadForm(false);
    } else {
      console.log("[SignupSheet] open — API_BASE=", API_BASE);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [visible]);

  const applyResearchResult = (r: {
    draft_id?: string | null;
    summary?: Record<string, any>;
    name?: string;
    company?: string | null;
    university?: string | null;
    linkedin_url?: string | null;
  }) => {
    setDraftId(r.draft_id || null);
    const summary = r.summary || {};
    setBriefing(summary);
    const personal = summary.personal_info || {};
    const roleBits = [summary.current_role, summary.current_company || r.company].filter(Boolean);
    setHeadline(roleBits.join(" · ") || picked?.role || headline);
    const hobbiesFrom = personal.hobbies || summary.hobbies;
    const sportsFrom = personal.sports_interests || personal.sports || summary.sports;
    const interestsFrom = summary.interests;
    if (Array.isArray(hobbiesFrom) && hobbiesFrom.length) {
      setHobbies((prev) => Array.from(new Set([...prev, ...hobbiesFrom.map(String)])));
    }
    if (Array.isArray(interestsFrom) && interestsFrom.length) {
      setInterests((prev) => Array.from(new Set([...prev, ...interestsFrom.map(String)])));
    }
    if (Array.isArray(sportsFrom) && sportsFrom.length) {
      setSports((prev) => Array.from(new Set([...prev, ...sportsFrom.map(String)])));
    }
    if (r.company) setCompany(String(r.company));
    if (r.university) setUniversity(String(r.university));
    if (r.linkedin_url) setLinkedin(String(r.linkedin_url));
    if (r.name) setName(String(r.name));
    setShowBadForm(false);
    setRatingNotes("");
    setStep(3);
  };

  const pollJob = (jobId: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const job = await api.publicResearchJob(jobId);
        if (typeof job.progress === "number") setProgress(job.progress);
        if (job.message) setPhase(job.message);
        if (job.status === "done" && job.result) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setProgress(1);
          setPhase("Done — review your public profile");
          setLoading(false);
          applyResearchResult(job.result);
          return;
        }
        if (job.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setLoading(false);
          setError(job.error || job.message || "Research failed");
          setStep(1);
        }
      } catch (e: any) {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setLoading(false);
        setError(e.message || "Could not check research progress");
        setStep(1);
      }
    }, 1500);
  };

  const findCandidates = async () => {
    setError("");
    if (!name.trim()) {
      setError("Enter your full name.");
      return;
    }
    setLoading(true);
    setCandidates([]);
    setMatchMode("");
    setMatchMessage("");
    setPicked(null);
    setStep(1);
    try {
      const started = await api.publicCandidatesStart({
        name: name.trim(),
        company: company.trim() || null,
        university: university.trim() || null,
        linkedin_url: linkedin.trim() || null,
      });
      const jobId = started.job_id;
      const applyResult = (data: any) => {
        const mode =
          data.match_mode ||
          (data.exact?.length ? "exact" : data.probable?.length ? "probable_only" : "none");
        const list =
          mode === "exact"
            ? data.exact || data.candidates || []
            : mode === "probable_only"
              ? data.probable || data.candidates || []
              : data.candidates || [];
        setMatchMode(mode);
        setMatchMessage(data.message || "");
        setCandidates(list);
      };
      // Progressive poll — show people as they arrive
      await new Promise<void>((resolve, reject) => {
        const timer = setInterval(async () => {
          try {
            const job = await api.publicCandidatesJob(jobId);
            if (job.result) applyResult(job.result);
            if (job.status === "done") {
              clearInterval(timer);
              if (job.result) applyResult(job.result);
              resolve();
            } else if (job.status === "error") {
              clearInterval(timer);
              reject(new Error(job.error || job.message || "Search failed"));
            }
          } catch (e: any) {
            clearInterval(timer);
            reject(e);
          }
        }, 900);
      });
    } catch (e: any) {
      setError(`${e.message || "Search failed"} (API: ${API_BASE})`);
    } finally {
      setLoading(false);
    }
  };

  const startResearchJob = async () => {
    setError("");
    const personName = (picked?.name || name).trim();
    const co = picked?.company || company.trim() || null;
    const li = picked?.linkedin_url || linkedin.trim() || null;
    const uni = university.trim() || null;
    if (!co && !uni && !li) {
      setError("Pick a candidate or add company / university / LinkedIn before researching.");
      return;
    }

    setStep(2);
    setLoading(true);
    resetResearchUi();
    setDraftId(null);
    setBriefing(null);
    setProgress(0.02);
    setPhase("Starting research…");

    try {
      const started = await api.publicResearchStart({
        name: personName,
        company: co,
        university: uni,
        linkedin_url: li,
        force_refresh: true,
      });
      pollJob(started.job_id);
    } catch (e: any) {
      setLoading(false);
      setError(e.message || "Could not start research");
      setStep(1);
    }
  };

  const redoWithFeedback = async () => {
    setError("");
    if (!draftId) {
      setError("Research draft missing — go back and research again.");
      return;
    }
    if (!ratingNotes.trim()) {
      setError("Tell us what was wrong so we can fix the next research.");
      return;
    }
    setLoading(true);
    setStep(2);
    resetResearchUi();
    setProgress(0.02);
    setPhase("Applying your corrections…");
    try {
      const res = await api.publicResearchFeedback({
        draft_id: draftId,
        rating: "bad",
        wrong_notes: ratingNotes.trim(),
        wrong_categories: ["signup_self_research"],
        auto_retry: true,
      });
      if (!res.job_id) {
        setLoading(false);
        setError(res.message || "Could not restart research");
        setStep(3);
        return;
      }
      setDraftId(null);
      setBriefing(null);
      pollJob(res.job_id);
    } catch (e: any) {
      setLoading(false);
      setError(e.message || "Could not re-research");
      setStep(3);
    }
  };

  const createAccount = async () => {
    setError("");
    if (!email.trim() || password.length < 6) {
      setError("Email and password (6+ chars) required.");
      return;
    }
    if (!emailVerifiedToken) {
      setError("Verify your email with the code we sent first.");
      return;
    }
    if (!draftId) {
      setError("Research draft missing — go back and research again.");
      return;
    }
    setLoading(true);
    try {
      const signup = await api.signup({
        name: name.trim(),
        email: email.trim(),
        password,
        company: company.trim() || "",
        university: university.trim() || "",
        linkedin_url: linkedin.trim() || "",
        place: "",
        location: "",
        headline: headline.trim(),
        hobbies,
        interests,
        sports,
        research_me: false,
        draft_id: draftId,
        email_verified_token: emailVerifiedToken,
        talking_goals: ["Find genuine common ground before meetings"],
      });
      await setSession(signup.token, signup.user);
      await refresh();
      onClose();
    } catch (e: any) {
      setError(e.message || "Could not create account");
    } finally {
      setLoading(false);
    }
  };

  const sendSignupOtp = async () => {
    setError("");
    if (!email.trim() || !email.includes("@")) {
      setError("Enter a valid email.");
      return;
    }
    setLoading(true);
    try {
      const res = await api.signupOtpSend(email.trim());
      setOtpHint(res.destination_hint || "");
      setDebugCode(res.debug_code || null);
      setEmailVerifiedToken(null);
      setOtpCode(res.debug_code || "");
      setStep(5);
    } catch (e: any) {
      setError(e.message || "Could not send code");
    } finally {
      setLoading(false);
    }
  };

  const verifySignupOtp = async () => {
    setError("");
    if (!otpCode.trim()) {
      setError("Enter the verification code.");
      return;
    }
    setLoading(true);
    try {
      const res = await api.signupOtpVerify(email.trim(), otpCode.trim());
      if (!res.email_verified_token) {
        setError("Verification failed — try again.");
        return;
      }
      setEmailVerifiedToken(res.email_verified_token);
    } catch (e: any) {
      setError(e.message || "Incorrect or expired code");
    } finally {
      setLoading(false);
    }
  };

  const addTip = (chip: (typeof TIP_CHIPS)[number]) => {
    if (chip.kind === "hobbies" && !hobbies.includes(chip.label)) setHobbies([...hobbies, chip.label]);
    if (chip.kind === "interests" && !interests.includes(chip.label)) setInterests([...interests, chip.label]);
    if (chip.kind === "sports" && !sports.includes(chip.label)) setSports([...sports, chip.label]);
  };

  const titles = [
    "Your name",
    "Is this you?",
    "Researching you",
    "Does this look right?",
    "Verify email",
    "Create account",
  ];

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ width: "100%" }}>
          <Pressable
            style={[styles.sheet, { backgroundColor: colors.chalk, borderColor: colors.line }]}
            onPress={(e) => e.stopPropagation()}
          >
            <View style={[styles.handle, { backgroundColor: colors.line }]} />
            <Text style={[styles.title, { color: colors.ink }]}>{titles[step]}</Text>
            <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 10, fontSize: 13 }}>
              Step {step + 1} of 6
            </Text>

            {step === 2 ? (
              <View style={[styles.progressBox, { backgroundColor: colors.mist, borderColor: colors.line }]}>
                <Text style={{ fontFamily: fonts.bodySemi, color: colors.forest, marginBottom: 6 }}>
                  {Math.round(progress * 100)}%
                </Text>
                <View style={[styles.barTrack, { backgroundColor: colors.chalk }]}>
                  <View
                    style={[
                      styles.barFill,
                      { width: `${Math.max(4, Math.round(progress * 100))}%`, backgroundColor: colors.ember },
                    ]}
                  />
                </View>
                <Text style={{ marginTop: 10, fontFamily: fonts.body, color: colors.moss }}>{phase || "Working…"}</Text>
                <Text style={{ marginTop: 8, fontFamily: fonts.body, color: colors.muted, lineHeight: 20 }}>
                  This usually takes 1–2 minutes. Add hobbies below — they’ll merge into your profile.
                </Text>
              </View>
            ) : null}

            <ScrollView style={{ maxHeight: 440 }} keyboardShouldPersistTaps="handled">
              {step === 0 && (
                <View>
                  <Body>We’ll find your public profile from your name, then research you before you create an account.</Body>
                  <View style={{ height: 12 }} />
                  <Field label="Full name" value={name} onChangeText={setName} placeholder="As it appears professionally" />
                </View>
              )}

              {step === 1 && (
                <View>
                  <Pressable onPress={() => setShowFilters((s) => !s)}>
                    <Text style={{ fontFamily: fonts.bodyMed, color: colors.leaf, marginBottom: 8 }}>
                      {showFilters ? "Hide filters" : "Optional filters (company / university / LinkedIn)"}
                    </Text>
                  </Pressable>
                  {showFilters ? (
                    <View>
                      <Field label="Company" value={company} onChangeText={setCompany} />
                      <Field label="University" value={university} onChangeText={setUniversity} />
                      <Field label="LinkedIn URL or id" autoCapitalize="none" value={linkedin} onChangeText={setLinkedin} />
                      <Button title="Re-search matches" onPress={findCandidates} loading={loading} style={{ marginBottom: 10 }} />
                    </View>
                  ) : null}

                  {matchMode === "probable_only" && matchMessage ? (
                    <Text style={{ fontFamily: fonts.bodyMed, color: colors.ember, marginBottom: 10, lineHeight: 20 }}>
                      {matchMessage}
                    </Text>
                  ) : matchMode === "exact" && candidates.length ? (
                    <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 10 }}>
                      Exact name matches — pick the one that is you.
                    </Text>
                  ) : null}

                  {candidates.map((c, i) => (
                    <Pressable
                      key={`${c.name}-${i}`}
                      onPress={() => {
                        setPicked(c);
                        if (c.company) setCompany(c.company);
                        if (c.linkedin_url) setLinkedin(c.linkedin_url);
                      }}
                      style={{
                        padding: 12,
                        borderRadius: 14,
                        borderWidth: 1,
                        borderColor: picked === c ? colors.forest : colors.line,
                        backgroundColor: picked === c ? colors.mist : colors.chalk,
                        marginBottom: 8,
                        flexDirection: "row",
                        gap: 12,
                        alignItems: "center",
                      }}
                    >
                      <CandidateAvatar name={c.name} photoUrl={c.photo_url} mist={colors.mist} forest={colors.forest} />
                      <View style={{ flex: 1 }}>
                        <Text style={{ fontFamily: fonts.bodySemi, color: colors.ink }}>{c.name}</Text>
                        <Text style={{ fontFamily: fonts.body, color: colors.muted, marginTop: 4, fontSize: 13 }}>
                          {[c.company, c.role].filter(Boolean).join(" · ") || c.context || ""}
                        </Text>
                      </View>
                    </Pressable>
                  ))}
                  {!candidates.length ? (
                    <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 8 }}>
                      No matches yet — try optional filters, or add company / LinkedIn manually.
                    </Text>
                  ) : null}
                </View>
              )}

              {step === 2 && (
                <View>
                  <Text style={{ fontFamily: fonts.bodySemi, color: colors.forest, marginBottom: 8 }}>
                    Add what you’ll actually talk about
                  </Text>
                  <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 10, lineHeight: 18 }}>
                    Tap a suggestion or type your own — these save with your account.
                  </Text>
                  <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                    {TIP_CHIPS.map((chip) => (
                      <Pressable
                        key={chip.label}
                        onPress={() => addTip(chip)}
                        style={{
                          backgroundColor: colors.chalk,
                          borderWidth: 1,
                          borderColor: colors.line,
                          paddingHorizontal: 12,
                          paddingVertical: 8,
                          borderRadius: 10,
                        }}
                      >
                        <Text style={{ fontFamily: fonts.bodyMed, color: colors.forest, fontSize: 13 }}>+ {chip.label}</Text>
                      </Pressable>
                    ))}
                  </View>
                  <ChipInput label="Hobbies" value={hobbies} onChange={setHobbies} />
                  <ChipInput label="Interests" value={interests} onChange={setInterests} />
                  <ChipInput label="Sports" value={sports} onChange={setSports} />
                  {loading ? <ActivityIndicator color={colors.forest} style={{ marginTop: 12 }} /> : null}
                </View>
              )}

              {step === 3 && (
                <View>
                  <Text style={{ fontFamily: fonts.body, color: colors.muted, marginBottom: 12, lineHeight: 20 }}>
                    Full researched profile — confirm it’s you, edit anything off, then continue to save.
                  </Text>
                  <ResearchBriefingPreview
                    summary={briefing}
                    name={name}
                    company={company}
                    linkedinUrl={linkedin}
                  />
                  <Field label="Headline" value={headline} onChangeText={setHeadline} />
                  <ChipInput label="Hobbies" value={hobbies} onChange={setHobbies} />
                  <ChipInput label="Interests" value={interests} onChange={setInterests} />
                  <ChipInput label="Sports" value={sports} onChange={setSports} />
                  {showBadForm ? (
                    <View style={{ marginTop: 8 }}>
                      <Field
                        label="What was wrong?"
                        value={ratingNotes}
                        onChangeText={setRatingNotes}
                        placeholder="Wrong person, wrong company, outdated role…"
                        multiline
                      />
                      <Button title="Fix & re-research" variant="ember" loading={loading} onPress={redoWithFeedback} />
                      <Button title="Cancel" variant="ghost" onPress={() => setShowBadForm(false)} disabled={loading} />
                    </View>
                  ) : null}
                </View>
              )}

              {step === 4 && (
                <View>
                  <Body>We’ll send a one-time code to verify this email before you create your account.</Body>
                  <View style={{ height: 12 }} />
                  <Field
                    label="Email"
                    autoCapitalize="none"
                    keyboardType="email-address"
                    value={email}
                    onChangeText={(t) => {
                      setEmail(t);
                      setEmailVerifiedToken(null);
                    }}
                  />
                </View>
              )}

              {step === 5 && (
                <View>
                  <Body>
                    {otpHint
                      ? `Enter the code we sent to ${otpHint}, then choose a password.`
                      : "Enter the verification code, then choose a password."}
                  </Body>
                  {debugCode ? (
                    <Text style={{ fontFamily: fonts.bodyMed, color: colors.moss, marginTop: 8, marginBottom: 4 }}>
                      Dev code: {debugCode}
                    </Text>
                  ) : null}
                  <View style={{ height: 8 }} />
                  <Field
                    label="Verification code"
                    autoCapitalize="none"
                    keyboardType="number-pad"
                    value={otpCode}
                    onChangeText={setOtpCode}
                    placeholder="6-digit code"
                  />
                  {!emailVerifiedToken ? (
                    <Button title="Verify code" onPress={verifySignupOtp} loading={loading} style={{ marginBottom: 8 }} />
                  ) : (
                    <Text style={{ fontFamily: fonts.bodyMed, color: colors.forest, marginBottom: 8 }}>
                      Email verified ✓
                    </Text>
                  )}
                  <Field label="Password" secureTextEntry value={password} onChangeText={setPassword} />
                  <ResearchBriefingPreview
                    summary={briefing}
                    name={name}
                    company={company}
                    linkedinUrl={linkedin}
                  />
                </View>
              )}

              {error ? (
                <Text style={{ color: colors.danger, fontFamily: fonts.bodyMed, marginVertical: 8 }}>{error}</Text>
              ) : null}
            </ScrollView>

            <View style={{ marginTop: 12, gap: 8 }}>
              {step === 0 && (
                <Button
                  title="Find me"
                  variant="ember"
                  loading={loading}
                  onPress={() => {
                    setError("");
                    if (!name.trim()) {
                      setError("Enter your full name.");
                      return;
                    }
                    findCandidates();
                  }}
                />
              )}
              {step === 1 && (
                <Button
                  title={candidates.length ? "Yes — research me" : "Find me"}
                  onPress={() => {
                    if (!candidates.length) {
                      findCandidates();
                      return;
                    }
                    if (!picked && !company.trim() && !university.trim() && !linkedin.trim()) {
                      setError("Pick a candidate or add a filter.");
                      return;
                    }
                    startResearchJob();
                  }}
                  loading={loading}
                  variant="ember"
                />
              )}
              {step === 3 && !showBadForm && (
                <>
                  <Button
                    title="Looks right — continue"
                    variant="ember"
                    onPress={() => {
                      setError("");
                      setStep(4);
                    }}
                  />
                  <Button title="Something’s wrong — fix & re-research" variant="ghost" onPress={() => setShowBadForm(true)} />
                </>
              )}
              {step === 4 && (
                <Button title="Send verification code" onPress={sendSignupOtp} loading={loading} variant="ember" />
              )}
              {step === 5 && (
                <Button
                  title="Create account & save profile"
                  onPress={createAccount}
                  loading={loading}
                  variant="ember"
                  disabled={!emailVerifiedToken}
                />
              )}
              {step > 0 && step !== 2 ? (
                <Button
                  title="Back"
                  variant="ghost"
                  onPress={() =>
                    setStep((s) => (s === 5 ? 4 : s === 4 ? 3 : s === 3 ? 1 : s - 1))
                  }
                  disabled={loading}
                />
              ) : null}
              <Button title="Close" variant="ghost" onPress={onClose} disabled={loading && step === 2} />
            </View>
          </Pressable>
        </KeyboardAvoidingView>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.5)" },
  sheet: {
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    borderWidth: 1,
    padding: space.lg,
    paddingBottom: space.xl,
    maxHeight: "94%",
  },
  handle: { alignSelf: "center", width: 40, height: 4, borderRadius: 2, marginBottom: 12 },
  title: { fontFamily: fonts.display, fontSize: 26, marginBottom: 4 },
  progressBox: {
    borderWidth: 1,
    borderRadius: 16,
    padding: 14,
    marginBottom: 12,
  },
  barTrack: { height: 12, borderRadius: 8, overflow: "hidden" },
  barFill: { height: 12, borderRadius: 8 },
});
