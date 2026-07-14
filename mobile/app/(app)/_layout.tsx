import { Tabs } from "expo-router";
import { Text, View } from "react-native";
import { fonts } from "../../lib/theme";
import { useTheme } from "../../lib/theme-context";

function TabIcon({ glyph, focused }: { glyph: string; focused: boolean }) {
  const { colors } = useTheme();
  return (
    <View
      style={{
        width: 28,
        height: 28,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 8,
        backgroundColor: focused ? colors.mist : "transparent",
      }}
    >
      <Text style={{ fontSize: 16, color: focused ? colors.ember : colors.muted }}>{glyph}</Text>
    </View>
  );
}

function TabLabel({ label, focused }: { label: string; focused: boolean }) {
  const { colors } = useTheme();
  return (
    <Text
      style={{
        fontFamily: focused ? fonts.bodySemi : fonts.body,
        fontSize: 11,
        color: focused ? colors.ember : colors.muted,
      }}
    >
      {label}
    </Text>
  );
}

export default function AppLayout() {
  const { colors } = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: colors.chalk,
          borderTopColor: colors.line,
          height: 64,
          paddingBottom: 8,
          paddingTop: 8,
        },
        tabBarActiveTintColor: colors.ember,
        tabBarInactiveTintColor: colors.muted,
      }}
    >
      <Tabs.Screen
        name="home"
        options={{
          title: "Search",
          tabBarLabel: ({ focused }) => <TabLabel label="Search" focused={focused} />,
          tabBarIcon: ({ focused }) => <TabIcon glyph="◎" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="crm"
        options={{
          title: "People",
          tabBarLabel: ({ focused }) => <TabLabel label="People" focused={focused} />,
          tabBarIcon: ({ focused }) => <TabIcon glyph="⌘" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: "You",
          tabBarLabel: ({ focused }) => <TabLabel label="You" focused={focused} />,
          tabBarIcon: ({ focused }) => <TabIcon glyph="◌" focused={focused} />,
        }}
      />
      <Tabs.Screen name="person/[name]" options={{ href: null }} />
    </Tabs>
  );
}
