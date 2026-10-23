from __future__ import annotations

import unittest

from unittest.mock import Mock

from scripts.fetch_public_data import osm_geometry, overpass_layer


class OsmGeometryTests(unittest.TestCase):
    def test_builds_line_from_overpass_way_geometry(self) -> None:
        geometry = osm_geometry(
            {
                "type": "way",
                "geometry": [
                    {"lon": 118.1, "lat": 25.2},
                    {"lon": 118.2, "lat": 25.3},
                ],
            },
            "line",
        )
        self.assertEqual(geometry.geom_type, "LineString")
        self.assertEqual(list(geometry.coords)[0], (118.1, 25.2))

    def test_uses_center_for_point_layer(self) -> None:
        geometry = osm_geometry(
            {"type": "way", "center": {"lon": 118.3, "lat": 25.4}}, "point"
        )
        self.assertEqual(geometry.geom_type, "Point")
        self.assertEqual((geometry.x, geometry.y), (118.3, 25.4))

    def test_returns_none_for_incomplete_way(self) -> None:
        self.assertIsNone(
            osm_geometry(
                {"type": "way", "geometry": [{"lon": 118.1, "lat": 25.2}]}, "line"
            )
        )

    def test_empty_overpass_result_has_a_geometry_column(self) -> None:
        response = Mock()
        response.json.return_value = {"elements": []}
        response.raise_for_status.return_value = None
        http = Mock()
        http.post.return_value = response
        layer = overpass_layer(http, "https://example.invalid", 1, "node(area.a);", "point")
        self.assertTrue(layer.empty)
        self.assertEqual(layer.crs.to_string(), "EPSG:4326")
        self.assertEqual(layer.geometry.name, "geometry")


if __name__ == "__main__":
    unittest.main()
