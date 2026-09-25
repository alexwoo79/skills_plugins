import QtQuick
import qs.Commons

// One "label   value" line inside the Proxy panel. Values wrap instead of
// eliding: an error message is the one thing the user needs in full.
Item {
  id: root

  property string label: ""
  property string value: ""
  property color labelColor: Color.foreground
  property color valueColor: Color.foreground
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.body
  property real labelWidth: Style.space(78)

  readonly property color dimLabel: Qt.darker(root.labelColor, 1.6)

  implicitHeight: Math.max(labelText.implicitHeight, valueText.implicitHeight)
  height: implicitHeight

  Text {
    id: labelText
    anchors.left: parent.left
    anchors.top: parent.top
    width: root.labelWidth
    text: root.label
    color: root.dimLabel
    font.family: root.fontFamily
    font.pixelSize: root.fontSize
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
  }

  Text {
    id: valueText
    anchors.left: labelText.right
    anchors.leftMargin: Style.space(8)
    anchors.right: parent.right
    anchors.top: parent.top
    text: root.value
    color: root.valueColor
    font.family: root.fontFamily
    font.pixelSize: root.fontSize
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
  }
}
