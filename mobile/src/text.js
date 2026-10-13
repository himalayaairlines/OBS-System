/**
 * Text and TextInput with Work Sans on iOS / Android.
 *
 * On the web a global CSS rule (src/fonts.js) already sets Work Sans
 * everywhere. Native apps have no such rule, so every screen imports Text and
 * TextInput from here instead of 'react-native'. The style's fontWeight picks
 * the matching registered family (WorkSans-400 ... WorkSans-800). A style that
 * sets its own fontFamily is left alone, and if the font could not be loaded
 * (offline first start) text simply uses the system font.
 */
import React from 'react';
import { Platform, StyleSheet, Text as RNText, TextInput as RNTextInput } from 'react-native';

import { nativeFamily } from './fonts';

function withWorkSans(style) {
  const flat = StyleSheet.flatten(style) || {};
  if (flat.fontFamily) return style;
  const family = nativeFamily(flat.fontWeight);
  if (!family) return style;
  // The weight lives in the family itself; a leftover fontWeight would make
  // Android synthesise a fake bold on top of it.
  return [style, { fontFamily: family, fontWeight: 'normal' }];
}

function WorkSansText(props) {
  return <RNText {...props} style={withWorkSans(props.style)} />;
}

function WorkSansTextInput(props) {
  return <RNTextInput {...props} style={withWorkSans(props.style)} />;
}

export const Text = Platform.OS === 'web' ? RNText : WorkSansText;
export const TextInput = Platform.OS === 'web' ? RNTextInput : WorkSansTextInput;
