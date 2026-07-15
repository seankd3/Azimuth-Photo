package app.azimuthphoto.mobile.ui.library

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import app.azimuthphoto.mobile.data.ArchiveApi
import app.azimuthphoto.mobile.data.ArchiveImage
import app.azimuthphoto.mobile.data.LibraryApi
import app.azimuthphoto.mobile.ui.Panel
import app.azimuthphoto.mobile.ui.TextPrimary
import app.azimuthphoto.mobile.ui.TextSecondary
import coil.compose.AsyncImage
import coil.request.ImageRequest

/** Visually-similar photos from the hub's embedding index. */
@Composable
fun SimilarScreen(
    archiveApi: ArchiveApi,
    library: LibraryApi,
    image: ArchiveImage,
    onBack: () -> Unit,
    onOpenPhotos: (List<ArchiveImage>, Int) -> Unit,
) {
    BackHandler(onBack = onBack)
    var results by remember(image.id) { mutableStateOf<List<ArchiveImage>?>(null) }
    LaunchedEffect(image.id) { results = library.similar(image.id) }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        Row(
            Modifier.fillMaxWidth().background(Panel).padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back", tint = TextPrimary)
            }
            Text("Similar photos", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
        }
        val list = results
        when {
            list == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            list.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("Nothing similar found.", color = TextSecondary)
            }
            else -> LazyVerticalGrid(
                columns = GridCells.Fixed(4),
                verticalArrangement = Arrangement.spacedBy(2.dp),
                horizontalArrangement = Arrangement.spacedBy(2.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                itemsIndexed(list, key = { _, img -> img.id }) { index, img ->
                    AsyncImage(
                        model = ImageRequest.Builder(LocalContext.current)
                            .data(archiveApi.thumbUrl(img)).crossfade(false).build(),
                        contentDescription = img.filename,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.aspectRatio(1f).background(Panel)
                            .clickable { onOpenPhotos(list, index) },
                    )
                }
            }
        }
    }
}
