import uuid
import os
from django.test import TestCase
from mock import MagicMock, patch
from importer.handlers.common.vector import import_with_ogr2ogr
from importer.handlers.geojson.exceptions import InvalidGeoJsonException
from importer.handlers.geojson.handler import GeoJsonFileHandler
from django.contrib.auth import get_user_model
from importer import project_dir
from geonode.upload.models import UploadParallelismLimit
from geonode.upload.api.exceptions import UploadParallelismLimitException
from geonode.base.populate_test_data import create_single_dataset
from osgeo import ogr


class TestGeoJsonFileHandler(TestCase):
    databases = ("default", "datastore")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.handler = GeoJsonFileHandler()
        cls.valid_geojson = f"{project_dir}/tests/fixture/valid.geojson"
        cls.invalid_geojson = f"{project_dir}/tests/fixture/invalid.geojson"
        cls.user, _ = get_user_model().objects.get_or_create(username="admin")
        cls.invalid_files = {"base_file": cls.invalid_geojson}
        cls.valid_files = {"base_file": cls.valid_geojson}
        cls.owner = get_user_model().objects.first()
        cls.layer = create_single_dataset(
            name="stazioni_metropolitana", owner=cls.owner
        )

    def test_task_list_is_the_expected_one(self):
        expected = (
            "start_import",
            "importer.import_resource",
            "importer.publish_resource",
            "importer.create_geonode_resource",
        )
        self.assertEqual(len(self.handler.ACTIONS["import"]), 4)
        self.assertTupleEqual(expected, self.handler.ACTIONS["import"])

    def test_task_list_is_the_expected_one_copy(self):
        expected = (
            "start_copy",
            "importer.copy_dynamic_model",
            "importer.copy_geonode_data_table",
            "importer.publish_resource",
            "importer.copy_geonode_resource",
        )
        self.assertEqual(len(self.handler.ACTIONS["copy"]), 5)
        self.assertTupleEqual(expected, self.handler.ACTIONS["copy"])

    def test_is_valid_should_raise_exception_if_the_parallelism_is_met(self):
        parallelism, created = UploadParallelismLimit.objects.get_or_create(
            slug="default_max_parallel_uploads"
        )
        old_value = parallelism.max_number
        try:
            UploadParallelismLimit.objects.filter(
                slug="default_max_parallel_uploads"
            ).update(max_number=0)

            with self.assertRaises(UploadParallelismLimitException):
                self.handler.is_valid(files=self.valid_files, user=self.user)

        finally:
            parallelism.max_number = old_value
            parallelism.save()

    def test_is_valid_should_pass_with_valid_geojson(self):
        self.handler.is_valid(files=self.valid_files, user=self.user)

    def test_is_valid_should_raise_exception_if_the_geojson_is_invalid(self):
        data = {
            "base_file": "/using/double/dot/in/the/name/is/an/error/file.invalid.geojson"
        }
        with self.assertRaises(InvalidGeoJsonException) as _exc:
            self.handler.is_valid(files=data, user=self.user)

        self.assertIsNotNone(_exc)
        self.assertTrue(
            "Please remove the additional dots in the filename"
            in str(_exc.exception.detail)
        )

    def test_is_valid_should_raise_exception_if_the_geojson_is_invalid_format(self):
        with self.assertRaises(InvalidGeoJsonException) as _exc:
            self.handler.is_valid(files=self.invalid_files, user=self.user)

        self.assertIsNotNone(_exc)
        self.assertTrue(
            "The provided GeoJson is not valid" in str(_exc.exception.detail)
        )

    def test_get_ogr2ogr_driver_should_return_the_expected_driver(self):
        expected = ogr.GetDriverByName("GEOJSON")
        actual = self.handler.get_ogr2ogr_driver()
        self.assertEqual(type(expected), type(actual))

    def test_can_handle_should_return_true_for_geojson(self):
        actual = self.handler.can_handle(self.valid_files)
        self.assertTrue(actual)

    def test_can_handle_should_return_false_for_other_files(self):
        actual = self.handler.can_handle({"base_file": "random.gpkg"})
        self.assertFalse(actual)

    @patch("importer.handlers.common.vector.Popen")
    def test_import_with_ogr2ogr_without_errors_should_call_the_right_command(
        self, _open
    ):
        _uuid = uuid.uuid4()

        comm = MagicMock()
        comm.communicate.return_value = b"", b""
        _open.return_value = comm

        _task, alternate, execution_id = import_with_ogr2ogr(
            execution_id=str(_uuid),
            files=self.valid_files,
            original_name="dataset",
            handler_module_path=str(self.handler),
            ovverwrite_layer=False,
            alternate="alternate",
        )

        self.assertEqual("ogr2ogr", _task)
        self.assertEqual(alternate, "alternate")
        self.assertEqual(str(_uuid), execution_id)

        _open.assert_called_once()
        _open.assert_called_with(
            f"/usr/bin/ogr2ogr --config PG_USE_COPY YES -f PostgreSQL PG:\" dbname='test_geonode_data' host="
            + os.getenv("DATABASE_HOST", "localhost")
            + " port=5432 user='geonode_data' password='geonode_data' \" \""
            + self.valid_files.get("base_file")
            + '" -nln alternate "dataset" -lco GEOMETRY_NAME=geometry',
            stdout=-1,
            stderr=-1,
            shell=True,  # noqa
        )
