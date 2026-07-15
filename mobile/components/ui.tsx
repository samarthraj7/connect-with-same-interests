import React from "react";
import {
  ActivityIndicator,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
  ViewStyle,
} from "react-native";
import { fonts, space } from "../lib/theme";
import { useTheme } from "../lib/theme-context";

export function BrandMark({ size = "lg" }: { size?: "lg" | "sm" }) {
  const { colors } = useTheme();
  const big = size === "lg";
  return (
    <View>
      <Text
        style={[
          { fontFamily: fonts.display, color: colors.ink },
          big ? { fontSize: 40, lineHeight: 46, letterSpacing: -0.8 } : { fontSize: 22, letterSpacing: -0.4 },
        ]}
      >
        Connect Deeply
      </Text>
      {big ? (
        <Text
          style={{
            marginTop: 8,
            fontFamily: fonts.body,
            fontSize: 16,
            lineHeight: 22,
            color: colors.muted,
            maxWidth: 280,
          }}
        >
          Meet people with something real in common.
        </Text>
      ) : null}
    </View>
  );
}

export function Field(props: TextInputProps & { label: string }) {
  const { colors } = useTheme();
  const { label, style, ...rest } = props;
  return (
    <View style={{ marginBottom: space.md }}>
      <Text
        style={{
          fontFamily: fonts.bodyMed,
          fontSize: 13,
          color: colors.moss,
          marginBottom: 6,
          textTransform: "uppercase",
          letterSpacing: 0.8,
        }}
      >
        {label}
      </Text>
      <TextInput
        placeholderTextColor={colors.muted}
        style={[
          {
            fontFamily: fonts.body,
            fontSize: 16,
            color: colors.ink,
            backgroundColor: colors.chalk,
            borderWidth: 1,
            borderColor: colors.line,
            borderRadius: 14,
            paddingHorizontal: 14,
            paddingVertical: 12,
            opacity: 0.95,
          },
          style,
        ]}
        {...rest}
      />
    </View>
  );
}

export function ChipInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const { colors } = useTheme();
  const [draft, setDraft] = React.useState("");
  const add = () => {
    const t = draft.trim();
    if (!t) return;
    if (!value.includes(t)) onChange([...value, t]);
    setDraft("");
  };
  return (
    <View style={{ marginBottom: space.md }}>
      <Text
        style={{
          fontFamily: fonts.bodyMed,
          fontSize: 13,
          color: colors.moss,
          marginBottom: 6,
          textTransform: "uppercase",
          letterSpacing: 0.8,
        }}
      >
        {label}
      </Text>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
        {value.map((v) => (
          <Pressable
            key={v}
            onPress={() => onChange(value.filter((x) => x !== v))}
            style={{
              backgroundColor: colors.mist,
              paddingHorizontal: 10,
              paddingVertical: 6,
              borderRadius: 10,
            }}
          >
            <Text style={{ fontFamily: fonts.bodyMed, color: colors.forest, fontSize: 13 }}>{v} ×</Text>
          </Pressable>
        ))}
      </View>
      <TextInput
        value={draft}
        onChangeText={setDraft}
        onSubmitEditing={add}
        placeholder={placeholder || "Type and hit return"}
        placeholderTextColor={colors.muted}
        style={{
          fontFamily: fonts.body,
          fontSize: 16,
          color: colors.ink,
          backgroundColor: colors.chalk,
          borderWidth: 1,
          borderColor: colors.line,
          borderRadius: 14,
          paddingHorizontal: 14,
          paddingVertical: 12,
        }}
        returnKeyType="done"
      />
    </View>
  );
}

export function Button({
  title,
  onPress,
  variant = "primary",
  loading,
  disabled,
  style,
}: {
  title: string;
  onPress: () => void;
  variant?: "primary" | "ghost" | "ember";
  loading?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  const { colors } = useTheme();
  const bg =
    variant === "primary" ? colors.forest : variant === "ember" ? colors.ember : "transparent";
  const color = variant === "ghost" ? colors.forest : colors.chalk;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        {
          borderRadius: 16,
          paddingVertical: 14,
          paddingHorizontal: 18,
          alignItems: "center",
          justifyContent: "center",
          minHeight: 52,
          backgroundColor: bg,
          opacity: pressed || disabled ? 0.7 : 1,
        },
        variant === "ghost" && { borderWidth: 1, borderColor: colors.line },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={color} />
      ) : (
        <Text style={{ fontFamily: fonts.bodySemi, fontSize: 16, color }}>{title}</Text>
      )}
    </Pressable>
  );
}

export function SectionTitle({ children }: { children: string }) {
  const { colors } = useTheme();
  return (
    <Text
      style={{
        fontFamily: fonts.display,
        fontSize: 22,
        color: colors.ink,
        marginBottom: 10,
        marginTop: 8,
      }}
    >
      {children}
    </Text>
  );
}

export function Body({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
  if (typeof children === "string") {
    return <LinkedText text={children} style={{ fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.ink }} />;
  }
  return (
    <Text style={{ fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.ink }}>
      {children}
    </Text>
  );
}

export function Bullet({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
  return (
    <View style={{ flexDirection: "row", gap: 8, marginBottom: 8 }}>
      <Text style={{ fontFamily: fonts.bodySemi, color: colors.ember, fontSize: 18, lineHeight: 22 }}>
        ·
      </Text>
      {typeof children === "string" ? (
        <LinkedText
          text={children}
          style={{ flex: 1, fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.ink }}
        />
      ) : (
        <Text style={{ flex: 1, fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.ink }}>
          {children}
        </Text>
      )}
    </View>
  );
}

/** Strip raw URLs from research text; show them as "source" hyperlinks instead. */
export function LinkedText({
  text,
  style,
}: {
  text: string;
  style?: Text["props"]["style"];
}) {
  const { colors } = useTheme();
  const { plain, urls } = stripUrlsToLinks(text);
  if (!urls.length) {
    return <Text style={style}>{plain || text}</Text>;
  }
  return (
    <Text style={style}>
      {plain}
      {plain && urls.length ? " " : null}
      {urls.map((url, i) => (
        <Text
          key={`${url}-${i}`}
          onPress={() => Linking.openURL(url).catch(() => undefined)}
          style={{
            color: colors.leaf,
            textDecorationLine: "underline",
            fontFamily: fonts.bodyMed,
          }}
        >
          {i === 0 ? "source" : ` · source ${i + 1}`}
        </Text>
      ))}
    </Text>
  );
}

/** Visible label for a standalone URL (no raw link printed). */
export function UrlLink({
  url,
  label = "Open link",
}: {
  url?: string | null;
  label?: string;
}) {
  const { colors } = useTheme();
  if (!url) return null;
  return (
    <Text
      onPress={() => Linking.openURL(url).catch(() => undefined)}
      style={{
        fontFamily: fonts.bodyMed,
        fontSize: 15,
        lineHeight: 22,
        color: colors.leaf,
        textDecorationLine: "underline",
      }}
    >
      {label}
    </Text>
  );
}

const URL_IN_PARENS = /\s*\((https?:\/\/[^)\s]+)\)/gi;
const BARE_URL = /https?:\/\/[^\s)\]>"']+/gi;

export function stripUrlsToLinks(raw: string): { plain: string; urls: string[] } {
  if (!raw) return { plain: "", urls: [] };
  const urls: string[] = [];
  let plain = raw.replace(URL_IN_PARENS, (_m, url: string) => {
    urls.push(url);
    return "";
  });
  plain = plain.replace(BARE_URL, (url) => {
    urls.push(url);
    return "";
  });
  plain = plain
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;:!?])/g, "$1")
    .replace(/\(\s*\)/g, "")
    .trim();
  // de-dupe preserving order
  const seen = new Set<string>();
  const uniq: string[] = [];
  for (const u of urls) {
    if (seen.has(u)) continue;
    seen.add(u);
    uniq.push(u);
  }
  return { plain, urls: uniq };
}

// unused StyleSheet kept out — themed components use inline theme colors
void StyleSheet;
